#!/usr/bin/env python3
"""Verify that an endpoint serves the model it claims, per request.

A reseller gateway can serve several backends behind one URL and one model
name. The response id says which protocol the upstream speaks, not which
model it was, and the self-report says which model it thinks it is, not which
one it is. Two facts survive both:

  frontier  the last dated event the model can place. Asking for the month
            and year of a known event is machine-checkable; asking the model
            for its own cutoff is not, because it refuses, hedges, or answers
            with whatever the harness in front of it suggests.

  accounting whether the upstream reports its own token accounting. A native
            Anthropic API always returns cache_read_input_tokens. A gateway
            that translates to an OpenAI-compatible backend estimates the
            input count, echoes the gateway's own x-kpa-input-tokens-estimate
            back as usage.input_tokens, and omits the cache counter.

Both are checked here, per model id, with the id family recorded alongside.

Usage:
    export KPA_CLAUDE_URL="https://api.example.com/v1"
    export KPA_CLAUDE_KEY="..."
    export CLAUDE_MODEL="claude-opus-5-5"
    python3 trust.py --model claude-opus-5-5
    python3 trust.py --model claude-opus-5-5 --model gpt-5-6-sol --samples 2
    python3 trust.py --all
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

BASE = (os.environ.get("KPA_CLAUDE_URL")
        or os.environ.get("KPA_URL")
        or "https://api.kpalabz.com/v1").rstrip("/")
KEY = (os.environ.get("KPA_CLAUDE_KEY")
       or os.environ.get("KPA_KEY")
       or "").strip()
DIALECT = os.environ.get("KPA_DIALECT", "messages")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "32000"))

# Dated facts, newest last. Each is checkable by asking for its month and
# year. The last one the model places correctly is the edge of its knowledge.
FACTS = [
    ("2024-11", "the 2024 United States presidential election was won by Donald Trump"),
    ("2025-01", "DeepSeek released the open-weight reasoning model R1"),
    ("2025-04", "Meta released the Llama 4 family of models"),
    ("2025-07", "an AI model reached gold-medal standard at the 2025 International "
                "Mathematical Olympiad"),
    ("2025-08", "OpenAI released the GPT-5 model"),
    ("2025-11", "Anthropic released Claude Opus 4.5"),
    ("2026-02", "Anthropic released Claude Opus 4.6"),
]
# An event that has not happened. A model that places it is confabulating.
FUTURE = [("2027-03", "Anthropic released a model called Claude Opus 7")]
QUESTION = ("In which month and year did the following happen? Reply with only "
            "the month and year, like 2025-08.\n%s")

NATIVE = "msg_01"
MINTED = "msg_kpa_"
GENERIC = "gw-"
OPENAI = "chatcmpl-"
RESPONSES = "resp_"


def id_family(identifier):
    if identifier.startswith(NATIVE):
        return "A-native"
    if identifier.startswith(MINTED) or identifier.startswith(GENERIC):
        return "B-minted"
    if identifier.startswith(OPENAI) or identifier.startswith(RESPONSES):
        return "B-openai"
    return "unknown"


def ask(model, prompt, max_tokens=400):
    if DIALECT == "messages":
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"x-api-key": KEY, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        path = BASE + "/messages"
    else:
        body = {"model": model, "max_completion_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"Authorization": "Bearer " + KEY, "content-type": "application/json"}
        path = BASE + "/chat/completions"
    request = urllib.request.Request(path, data=json.dumps(body).encode(), headers=headers)
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                time.sleep(8)
                continue
            return {"text": "", "id": "", "usage": {}, "error": "HTTP %d" % exc.code}
        except Exception as exc:
            return {"text": "", "id": "", "usage": {}, "error": str(exc)[:80]}
    else:
        return {"text": "", "id": "", "usage": {}, "error": "retries exhausted"}

    if "choices" in payload:
        text = (payload["choices"][0].get("message") or {}).get("content") or ""
    else:
        text = " ".join(b.get("text", "") for b in payload.get("content", [])
                        if b.get("type") == "text")
    return {"text": text.strip(), "id": payload.get("id", ""),
            "usage": payload.get("usage", {}) or {}, "error": ""}


def dated(answer, expected):
    return expected in answer.lower()


def check(model, samples=1):
    """Return the verdict dict for one model id."""
    edge = None
    families = []
    native_calls = 0
    total_calls = 0
    for _ in range(samples):
        for expected, statement in FACTS:
            row = ask(model, QUESTION % statement)
            total_calls += 1
            families.append(id_family(row["id"]))
            usage = row.get("usage") or {}
            # A native Anthropic API always reports cache_read_input_tokens.
            # thinking_tokens only appears when the model actually thought,
            # so it cannot be required.
            if "cache_read_input_tokens" in usage:
                native_calls += 1
            if dated(row["text"], expected):
                edge = expected
            time.sleep(0.4)
    future_hits = 0
    for _ in range(samples):
        row = ask(model, QUESTION % FUTURE[0][1])
        total_calls += 1
        if dated(row["text"], FUTURE[0][0]):
            future_hits += 1
        time.sleep(0.4)

    if future_hits:
        verdict = "UNRELIABLE: claims an event that has not happened"
    elif edge is None:
        verdict = "STALE: cannot place any dated fact"
    elif edge >= "2026-02":
        verdict = "CURRENT: frontier %s" % edge
    elif edge >= "2025-01":
        verdict = "PARTIAL: frontier %s" % edge
    else:
        verdict = "STALE: frontier %s" % edge

    return {
        "model": model,
        "frontier": edge or "none",
        "verdict": verdict,
        "id_families": sorted(set(families)),
        "native_accounting": "%d/%d" % (native_calls, total_calls),
        "future_leaks": future_hits,
    }


def list_models():
    request = urllib.request.Request(
        BASE + "/models",
        headers={"x-api-key": KEY, "Authorization": "Bearer " + KEY,
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        print("could not list models:", exc)
        return []
    return [model.get("id") for model in payload.get("data", []) if model.get("id")]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", default=[],
                        help="model id to check, repeatable")
    parser.add_argument("--all", action="store_true", help="check every id the key exposes")
    parser.add_argument("--samples", type=int, default=1, help="samples per fact")
    parser.add_argument("--json", help="write the results to this path")
    args = parser.parse_args()

    if not KEY:
        parser.error("no key: set KPA_CLAUDE_KEY")
    models = args.model
    if args.all or not models:
        models = list_models() or [os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")]

    results = []
    for model in models:
        row = check(model, args.samples)
        results.append(row)
        print("%-24s %-44s ids=%-28s native_accounting=%s"
              % (model, row["verdict"], ",".join(row["id_families"]),
                 row["native_accounting"]))
        if args.json:
            with open(args.json, "w") as handle:
                json.dump(results, handle, indent=1)
    if args.json:
        print("\nwrote", args.json)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Classify every model on a gateway into family A or family B.

Family A: the model self-reports Claude, Anthropic, ChatGPT or OpenAI, and
          its behaviour matches that claim (recent knowledge cutoff, no
          conflicting vendor identity).
Family B: everything else caught. The model self-reports some other vendor
          (StepFun, Google, Alibaba Cloud, ...) or its behaviour contradicts
          its own identity claim.

The gateway behind one URL can serve several upstreams and the family a given
request lands on is not always the same, so every model is probed several times
and the verdict is based on the distribution, not one reply. Self-report alone
is never trusted: the cutoff and the election question are checked against it.

Usage:
    export KPA_URL="https://api.example.com/v1"
    export KPA_CLAUDE_KEY="..."
    export KPA_GPT_KEY="..."
    python3 classify_models.py
    python3 classify_models.py --samples 3 --json out/classification.json
"""

import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WS = re.compile(r"\s+")

CLAUDE_KEY = os.environ.get("KPA_CLAUDE_KEY") or ""
GPT_KEY = os.environ.get("KPA_GPT_KEY") or ""
URL = (os.environ.get("KPA_URL") or os.environ.get("KPA_CLAUDE_URL") or "").rstrip("/")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
GPT_MODEL = os.environ.get("GPT_MODEL", "gpt-5-6-sol")

GENUINE = {
    "anthropic": "Anthropic",
    "claude": "Anthropic",
    "openai": "OpenAI",
    "chatgpt": "OpenAI",
}


def post(path, headers, body, timeout=300):
    request = urllib.request.Request(
        URL + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            return {"_err": "HTTP %d" % exc.code}
    except Exception as exc:
        return {"_err": str(exc)}


def text_of(payload):
    if "content" in payload:
        return WS.sub(" ", "".join(b.get("text", "") for b in payload.get("content", [])
                                   if b.get("type") == "text")).strip()
    try:
        return WS.sub(" ", payload["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""


def id_prefix(payload):
    """Classify the upstream by response id shape.

    msg_01...      native Anthropic id, upstream speaks Anthropic natively
    msg_kpa_...    Anthropic-shaped id minted by the gateway, upstream is a
                   translation layer over something else
    chatcmpl-...   OpenAI-shaped id, upstream is an OpenAI-compatible backend
    """
    identifier = payload.get("id", "")
    if identifier.startswith("msg_kpa_"):
        return "msg_kpa_"
    if identifier.startswith("msg_"):
        return "msg_"
    if identifier.startswith("chatcmpl-"):
        return "chatcmpl-"
    return "none"


def list_models(key):
    payload = post("/models", {"x-api-key": key}, None) if False else None
    request = urllib.request.Request(
        URL + "/models", headers={"x-api-key": key}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read())
        return [entry.get("id") for entry in data.get("data", []) if entry.get("id")]
    except Exception:
        return []


class Probe:
    """One model, one dialect, one key."""

    def __init__(self, model, dialect, key):
        self.model = model
        self.dialect = dialect
        self.key = key

    def ask(self, prompt, max_tokens=2000):
        if self.dialect == "messages":
            body = {"model": self.model, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]}
            return post("/messages", {"x-api-key": self.key,
                                      "anthropic-version": "2023-06-01"}, body)
        body = {"model": self.model, "max_completion_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        return post("/chat/completions", {"Authorization": "Bearer " + self.key}, body)

    def text(self, prompt):
        return text_of(self.ask(prompt))

    def sample(self):
        identity = self.text("Which company created you? Answer with the name only.")
        cutoff = self.text("What is your knowledge cutoff date? Answer as YYYY-MM.")
        election = self.text("Who won the 2024 US presidential election? One short sentence.")
        payload = self.ask("Reply with exactly: PONG", max_tokens=600)
        return {
            "identity": identity[:60],
            "cutoff": cutoff[:14],
            "election_ok": "trump" in election.lower(),
            "id_prefix": id_prefix(payload),
            "has_thinking_tokens": "thinking_tokens" in (payload.get("usage") or {}),
            "error": payload.get("_err"),
        }


def classify_vendor(identity):
    low = identity.lower()
    for token, vendor in GENUINE.items():
        if token in low:
            return vendor
    for token in ("stepfun", "google", "alibaba", "qwen", "deepseek", "minimax",
                  "llama", "mistral", "zhipu", "moonshot", "baidu", "ernie"):
        if token in low:
            return "other:" + token
    return "unknown"


def classify_model(probe, samples):
    rows = [probe.sample() for _ in range(samples)]
    vendors = collections.Counter(classify_vendor(row["identity"]) for row in rows)
    prefixes = collections.Counter(row["id_prefix"] for row in rows)
    cutoffs = sorted({row["cutoff"] for row in rows if row["cutoff"]})
    elections = sum(1 for row in rows if row["election_ok"])

    dominant = vendors.most_common(1)[0] if vendors else ("unknown", 0)
    genuine_share = sum(count for vendor, count in vendors.items()
                         if vendor in GENUINE.values())

    # A self-report is not enough. A model that claims Claude or ChatGPT but
    # cannot place a 2024 election it should know about is not that model.
    # Cutoff and election are the behavioural checks.
    if dominant[0] in GENUINE.values() and genuine_share >= max(2, len(rows) - 1):
        label = GENUINE.get(dominant[0], dominant[0])
        if elections == len(rows) and cutoffs and max(cutoffs) >= "2025":
            family = "A"
        else:
            family = "B"
            label = label + " (claim inconsistent with behaviour)"
    else:
        family = "B"
        label = dominant[0]

    return {
        "model": probe.model,
        "dialect": probe.dialect,
        "family": family,
        "label": label,
        "vendors": dict(vendors),
        "id_prefixes": dict(prefixes),
        "cutoffs": cutoffs,
        "election_correct": elections,
        "samples": len(rows),
        "has_thinking_tokens": any(row["has_thinking_tokens"] for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--models", default="", help="comma separated model ids to probe")
    parser.add_argument("--json", default="", help="write the full report here")
    parser.add_argument("--markdown", default="", help="write a markdown table here")
    args = parser.parse_args()

    if not URL:
        sys.exit("Set KPA_URL (or KPA_CLAUDE_URL).")
    if not CLAUDE_KEY and not GPT_KEY:
        sys.exit("Set KPA_CLAUDE_KEY and/or KPA_GPT_KEY.")

    claude_models = list_models(CLAUDE_KEY) if CLAUDE_KEY else []
    gpt_models = list_models(GPT_KEY) if GPT_KEY else []
    if args.models:
        wanted = [m.strip() for m in args.models.split(",") if m.strip()]
        claude_models = [m for m in claude_models if m in wanted]
        gpt_models = [m for m in gpt_models if m in wanted]

    print("claude key exposes: %d models" % len(claude_models))
    print("gpt key exposes   : %d models" % len(gpt_models))
    print("samples per model : %d" % args.samples)
    print("")

    results = []
    for model in claude_models:
        probe = Probe(model, "messages", CLAUDE_KEY)
        results.append(classify_model(probe, args.samples))
        last = results[-1]
        print("  %-22s family=%s label=%-14s vendors=%s cutoffs=%s election=%d/%d"
              % (model, last["family"], last["label"], last["vendors"],
                 ",".join(last["cutoffs"]) or "?", last["election_correct"], last["samples"]))
    for model in gpt_models:
        probe = Probe(model, "chat", GPT_KEY)
        results.append(classify_model(probe, args.samples))
        last = results[-1]
        print("  %-22s family=%s label=%-14s vendors=%s cutoffs=%s election=%d/%d"
              % (model, last["family"], last["label"], last["vendors"],
                 ",".join(last["cutoffs"]) or "?", last["election_correct"], last["samples"]))

    family_a = [r for r in results if r["family"] == "A"]
    family_b = [r for r in results if r["family"] == "B"]

    print("")
    print("=" * 96)
    print("FAMILY A  (self-reports Claude / ChatGPT and behaves consistently)  %d models" % len(family_a))
    print("=" * 96)
    for row in family_a:
        print("  %-22s %-10s cutoffs=%-28s election=%d/%d  id=%s"
              % (row["model"], row["label"], ",".join(row["cutoffs"]) or "?",
                 row["election_correct"], row["samples"],
                 ",".join(row["id_prefixes"])))
    print("")
    print("=" * 96)
    print("FAMILY B  (everything else caught)  %d models" % len(family_b))
    print("=" * 96)
    for row in family_b:
        print("  %-22s %-14s vendors=%-34s cutoffs=%s election=%d/%d"
              % (row["model"], row["label"], str(row["vendors"]),
                 ",".join(row["cutoffs"]) or "?", row["election_correct"], row["samples"]))

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as handle:
            json.dump(results, handle, indent=2)
        print("")
        print("report written to %s" % args.json)

    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        lines = ["| model | dialect | family | label | cutoffs | 2024 election | id prefix |",
                 "|---|---|---|---|---|---|---|"]
        for row in results:
            lines.append("| `%s` | %s | **%s** | %s | %s | %d/%d | %s |" % (
                row["model"], row["dialect"], row["family"], row["label"],
                ", ".join(row["cutoffs"]) or "?", row["election_correct"], row["samples"],
                ", ".join(row["id_prefixes"])))
        with open(args.markdown, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        print("markdown written to %s" % args.markdown)


if __name__ == "__main__":
    main()

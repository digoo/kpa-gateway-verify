#!/usr/bin/env python3
"""Full frontier sweep with no tools, no agent loop, no browsing.

Direct API calls only. Whatever the model answers comes from its weights.
This is the measurement that counts: the opencode-based sweep allowed the
model to search, so it measured the search engine, not the model.

Usage: python3 frontier_sweep_direct.py [model ...]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

CLAUDE_KEY = open("/Users/rodrigo/.config/opencode/secrets/kpalabz-claude.key").read().strip()
BASE = "https://api.kpalabz.com/v1"

FACTS = [
    ("2024-11", "the 2024 United States presidential election was won by Donald Trump"),
    ("2025-01", "DeepSeek released the open-weight reasoning model R1"),
    ("2025-04", "Meta released the Llama 4 family of models"),
    ("2025-07", "an AI model reached gold-medal standard at the 2025 International "
                "Mathematical Olympiad"),
    ("2025-08", "OpenAI released the GPT-5 model"),
    ("2025-11", "Anthropic released Claude Opus 4.5"),
    ("2026-02", "Anthropic released Claude Opus 4.6"),
    ("2026-06", "Anthropic's Mythos-tier models were first released"),
]
FUTURE = [("2027-03", "Anthropic released a model called Claude Opus 7")]
QUESTION = ("In which month and year did the following happen? Reply with only "
            "the month and year, like 2025-08.\n%s")


def ask(model, prompt, max_tokens=300):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        BASE + "/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                time.sleep(8)
                continue
            return {"text": "", "id": "", "usage": {}}
        except Exception:
            return {"text": "", "id": "", "usage": {}}
    else:
        return {"text": "", "id": "", "usage": {}}
    text = " ".join(b.get("text", "") for b in payload.get("content", [])
                    if b.get("type") == "text")
    return {"text": text.strip(), "id": payload.get("id", ""),
            "usage": payload.get("usage", {}) or {}}


def family(identifier):
    if identifier.startswith("msg_01"):
        return "A"
    if identifier.startswith("msg_kpa_") or identifier.startswith("gw-"):
        return "B-minted"
    if identifier.startswith("chatcmpl-") or identifier.startswith("resp_"):
        return "B-openai"
    return "?"


MODELS = sys.argv[1:] or [
    "claude-fable-5-2", "claude-fable-5-1", "claude-fable-5",
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-haiku-5-5", "claude-haiku-4-5",
    "gpt-5-6-sol", "gpt-5-6-terra", "gpt-5-6-luna", "gpt-6-astra",
    "gpt-5-5", "gpt-5-4", "gpt-5-4-mini",
]
results = []
for model in MODELS:
    edge = None
    families = []
    native = 0
    for expected, statement in FACTS:
        row = ask(model, QUESTION % statement)
        families.append(family(row["id"]))
        if "cache_read_input_tokens" in row["usage"]:
            native += 1
        if expected in row["text"].lower():
            edge = expected
        time.sleep(0.4)
    leak = ask(model, QUESTION % FUTURE[0][1])
    leaked = "2027-03" in leak["text"].lower()
    if leaked:
        verdict = "UNRELIABLE (claims a future event)"
    elif edge is None:
        verdict = "STALE (no dated fact confirmed)"
    elif edge >= "2026-02":
        verdict = "CURRENT"
    elif edge >= "2025-08":
        verdict = "PARTIAL (frontier %s)" % edge
    else:
        verdict = "STALE (frontier %s)" % edge
    row = {"model": model, "frontier": edge or "none", "verdict": verdict,
           "native": native, "of": len(FACTS), "families": sorted(set(families)),
           "leak": leaked}
    results.append(row)
    print("%-20s %-9s %-34s native %d/%d ids=%s"
          % (model, edge or "none", verdict, native, len(FACTS),
             ",".join(sorted(set(families)))))
    with open("/tmp/frontier_direct.json", "w") as handle:
        json.dump(results, handle, indent=1)
print("\nwrote /tmp/frontier_direct.json")

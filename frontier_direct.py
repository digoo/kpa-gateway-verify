#!/usr/bin/env python3
"""Knowledge frontier measured with NO tools and NO agent loop.

Direct API call: no system field, no tools, no browsing. Whatever the model
answers comes from its weights, so this is the real training-data frontier.
The earlier opencode-based frontier let the model browse, so it measured
search, not knowledge.

Usage: python3 frontier_direct.py [model ...]
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
            return {"text": "", "id": "", "usage": {}, "error": "HTTP %d" % exc.code}
        except Exception as exc:
            return {"text": "", "id": "", "usage": {}, "error": str(exc)[:60]}
    else:
        return {"text": "", "id": "", "usage": {}, "error": "retries exhausted"}
    text = " ".join(b.get("text", "") for b in payload.get("content", [])
                    if b.get("type") == "text")
    return {"text": text.strip(), "id": payload.get("id", ""),
            "usage": payload.get("usage", {}) or {}, "error": ""}


def family(identifier):
    if identifier.startswith("msg_01"):
        return "A"
    if identifier.startswith("msg_kpa_") or identifier.startswith("gw-"):
        return "B-minted"
    if identifier.startswith("chatcmpl-") or identifier.startswith("resp_"):
        return "B-openai"
    return "?"


for model in sys.argv[1:] or ["claude-fable-5-2", "claude-opus-5-5", "gpt-6-astra"]:
    print("\n=== %s" % model)
    edge = None
    for expected, statement in FACTS:
        row = ask(model, QUESTION % statement)
        hit = expected in row["text"].lower()
        if hit:
            edge = expected
        usage = row["usage"]
        print("  %-8s %-4s %-10s cache=%-8s %s"
              % (expected, "ok" if hit else "no", family(row["id"]),
                 usage.get("cache_read_input_tokens"),
                 row["text"][:50].replace("\n", " ")))
        time.sleep(0.5)
    print("  -> frontier: %s" % (edge or "none"))
    leak = ask(model, QUESTION % FUTURE[0][1])
    print("  future check -> %s" % leak["text"][:60].replace("\n", " "))

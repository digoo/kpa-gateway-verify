#!/usr/bin/env python3
"""Identity probe: what model is this, really?

Runs a direct API call with a single identity question and returns
the model's response verbatim. This is meant to be run periodically
and compared against a known-good baseline.

Usage:
    python3 identity_probe.py [model_id] [question]

    model_id  default: claude-fable-5-2
    question  default: "What model are you? Reply with just the model name."
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

CLAUDE_KEY = open("/Users/rodrigo/.config/opencode/secrets/kpalabz-claude.key").read().strip()
BASE = "https://api.kpalabz.com/v1"

DEFAULT_QUESTION = "What model are you? Reply with just the model name."


def ask(model, prompt, max_tokens=200, use_anthropic_header=False):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    data = json.dumps(body).encode()
    headers = {"x-api-key": CLAUDE_KEY,
               "content-type": "application/json"}
    if use_anthropic_header:
        headers["anthropic-version"] = "2023-06-01"
    request = urllib.request.Request(
        BASE + "/messages", data=data,
        headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        return {"error": str(exc)[:80], "text": "", "id": ""}
    text = " ".join(b.get("text", "") for b in payload.get("content", [])
                    if b.get("type") == "text")
    return {"text": text.strip(), "id": payload.get("id", ""), "error": ""}


def main():
    args = [a for a in sys.argv[1:] if a != "--compare"]
    model = args[0] if len(args) > 0 else "claude-fable-5-2"
    question = args[1] if len(args) > 1 else DEFAULT_QUESTION
    compare = "--compare" in sys.argv

    row = ask(model, question, use_anthropic_header=False)
    digest = hashlib.sha256(row["text"].encode()).hexdigest()[:12]
    result = {
        "model": model,
        "question": question,
        "answer": row["text"],
        "answer_hash": digest,
        "response_id": row["id"],
        "error": row["error"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "header": "none",
    }
    print(json.dumps(result, indent=1))

    if compare:
        row2 = ask(model, question, use_anthropic_header=True)
        result2 = {
            "model": model,
            "question": question,
            "answer": row2["text"],
            "answer_hash": hashlib.sha256(row2["text"].encode()).hexdigest()[:12],
            "response_id": row2["id"],
            "error": row2["error"],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "header": "anthropic-version:2023-06-01",
        }
        print(json.dumps(result2, indent=1))


if __name__ == "__main__":
    main()

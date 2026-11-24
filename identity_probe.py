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


def ask(model, prompt):
    body = {"model": model, "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        BASE + "/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        return {"error": str(exc)[:80], "text": "", "id": ""}
    text = " ".join(b.get("text", "") for b in payload.get("content", [])
                    if b.get("type") == "text")
    return {"text": text.strip(), "id": payload.get("id", ""), "error": ""}


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-fable-5-2"
    question = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUESTION

    row = ask(model, question)
    digest = hashlib.sha256(row["text"].encode()).hexdigest()[:12]
    result = {
        "model": model,
        "question": question,
        "answer": row["text"],
        "answer_hash": digest,
        "response_id": row["id"],
        "error": row["error"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

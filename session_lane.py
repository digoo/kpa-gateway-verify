#!/usr/bin/env python3
"""Which upstream family does the gateway serve for a given session?

Measured 2026-11-21 against https://api.kpalabz.com/v1 with the claude key:
the backend behind `claude-opus-5-5` is chosen from the `x-opencode-session`
header, and only an exactly matching session id reaches native Anthropic.
  exact session id                -> msg_01...  native Anthropic
  same id, one character changed -> msg_kpa_... gateway-translated backend
  no session header at all        -> msg_kpa_... gateway-translated backend
Request shape, system prompt, tools, thinking, max_tokens and every other
header make no difference. So a plain API client without a real opencode
session id is served a different model than opencode is.

Usage:
    OPENCODE_SESSION_ID=ses_... python3 session_lane.py
    python3 session_lane.py --session ses_...
    python3 session_lane.py --samples 3
"""
import argparse
import json
import os
import time
import urllib.request

BASE = (os.environ.get("KPA_CLAUDE_URL") or "https://api.kpalabz.com/v1").rstrip("/")
KEY = (os.environ.get("KPA_CLAUDE_KEY")
       or open(os.path.expanduser(
           "~/.config/opencode/secrets/kpalabz-claude.key")).read().strip())
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
NATIVE = "msg_01"
MINTED = "msg_kpa_"
OPENAI = "chatcmpl-"


def family(identifier):
    if identifier.startswith(NATIVE):
        return "A"
    if identifier.startswith(MINTED) or identifier.startswith(OPENAI):
        return "B"
    return "unknown"


def call(session_id):
    body = {
        "model": MODEL,
        "max_tokens": 64,
        "stream": True,
        "system": [{"type": "text", "text": "You are precise."} for _ in range(7)],
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
    }
    headers = {"x-api-key": KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    if session_id:
        headers["x-opencode-session"] = session_id
    request = urllib.request.Request(
        BASE + "/messages", data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode(errors="replace")
    identifier = ""
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except Exception:
            continue
        if obj.get("type") == "message_start":
            identifier = obj.get("message", {}).get("id", "")
    return identifier, family(identifier)


def mutate(session_id):
    stem = session_id[4:]
    position = min(5, len(stem) - 1)
    swapped = "a" if stem[position] != "a" else "b"
    return session_id[:4 + position] + swapped + stem[position + 1:]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", ""),
                        help="session id to test (default: $OPENCODE_SESSION_ID)")
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    if not args.session:
        parser.error("no session id given and $OPENCODE_SESSION_ID is unset")
    cases = [("exact", args.session),
             ("mutated", mutate(args.session)),
             ("absent", None)]
    for _ in range(args.samples):
        for name, session_id in cases:
            identifier, lane = call(session_id)
            print("%-8s -> family %s  %s" % (name, lane, identifier[:44]))
            time.sleep(1)
        print("")


if __name__ == "__main__":
    main()

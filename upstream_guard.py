#!/usr/bin/env python3
"""Guard proxy: put it between opencode and the gateway, and know which
upstream actually answered every request.

One gateway URL can serve several upstreams behind it. The response id tells
you which one answered:

  msg_01...      native Anthropic. The upstream speaks Anthropic natively.
  msg_kpa_...    Anthropic-shaped id minted by the gateway. The upstream is a
                 translation layer over something else.
  chatcmpl-...   OpenAI-shaped id. The upstream is an OpenAI-compatible
                 backend.

The last two are not the model you asked for, even though the request and the
response look right. This proxy logs every request with that verdict and can
reject the ones that landed on the wrong upstream, so a client never silently
receives a substitute.

Usage:
    python3 upstream_guard.py --port 8099 --expect claude
    python3 upstream_guard.py --port 8099 --expect gpt
    python3 upstream_guard.py --port 8099 --observe-only

Then point the client at http://127.0.0.1:8099/v1 instead of the gateway.
"""

import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WS = re.compile(r"\s+")

LOG_LOCK = threading.Lock()
LOG_PATH = os.environ.get("GUARD_LOG", "/tmp/upstream_guard.log")

NATIVE = "msg_01"
MINTED = "msg_kpa_"
OPENAI = "chatcmpl-"

FAMILY_A = {NATIVE}
FAMILY_B = {MINTED, OPENAI}


def log_line(payload):
    with LOG_LOCK:
        with open(LOG_PATH, "a") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")


def upstream_base():
    return (os.environ.get("KPA_UPSTREAM")
            or os.environ.get("KPA_CLAUDE_URL")
            or "https://api.kpalabz.com/v1").rstrip("/")


def id_family(identifier):
    if identifier.startswith(NATIVE):
        return "A"
    if identifier.startswith(MINTED):
        return "B"
    if identifier.startswith(OPENAI):
        return "B"
    return "unknown"


EXPECTED_FAMILY = {"claude": "A", "gpt": "B"}


def forward(path, headers, body, timeout=600):
    request = urllib.request.Request(
        upstream_base() + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except Exception as exc:
        return 502, json.dumps({"error": str(exc)}).encode(), {}


def parse_sse(raw):
    """Pull the id out of an Anthropic SSE stream."""
    for line in raw.decode(errors="replace").splitlines():
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
            return obj.get("message", {}).get("id", "")
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            request_body = json.loads(raw)
        except Exception:
            request_body = {}

        upstream_path = self.path
        if not upstream_path.startswith("/"):
            upstream_path = "/" + upstream_path
        if not upstream_path.startswith("/v1/"):
            upstream_path = "/v1" + upstream_path

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "connection", "accept-encoding"):
                continue
            headers[key] = value

        status, body, response_headers = forward(upstream_path, headers, raw)

        streaming = "text/event-stream" in (response_headers.get("Content-Type") or "")
        if streaming:
            identifier = parse_sse(body)
        else:
            try:
                identifier = json.loads(body).get("id", "")
            except Exception:
                identifier = ""

        family = id_family(identifier)
        system = request_body.get("system", "__ABSENT__")

        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": request_body.get("model", ""),
            "path": self.path,
            "system_type": type(system).__name__ if system != "__ABSENT__" else "absent",
            "system_blocks": len(system) if isinstance(system, list) else 0,
            "stream": bool(request_body.get("stream")),
            "has_tools": "tools" in request_body,
            "response_id": identifier[:44],
            "family": family,
            "status": status,
        }

        # Fail closed. A wrong-family response is a substitute model, so refuse
        # it instead of passing it to the client.
        wanted = EXPECTED_FAMILY.get(self.server.expected)
        if wanted and family != wanted:
            record["action"] = "rejected"
            record["reason"] = "expected family %s, got %s" % (wanted, family)
            log_line(record)
            print(json.dumps(record, default=str), flush=True)
            error = json.dumps({
                "type": "error",
                "error": {
                    "type": "upstream_family_mismatch",
                    "message": ("upstream returned family %s, expected %s. Response id was %r."
                                % (family, wanted, identifier)),
                },
            }).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error)))
            self.end_headers()
            self.wfile.write(error)
            return

        record["action"] = "accepted"
        log_line(record)
        print(json.dumps(record, default=str), flush=True)

        self.send_response(status)
        self.send_header("Content-Type",
                         response_headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(body)))
        for key, value in response_headers.items():
            if key.lower() in ("content-type", "content-length", "transfer-encoding",
                               "connection"):
                continue
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--expect", default="", choices=["", "claude", "gpt"],
                        help="claude = only accept native Anthropic ids, "
                             "gpt = only accept OpenAI-shaped ids")
    parser.add_argument("--observe-only", action="store_true",
                        help="log only, forward everything")
    args = parser.parse_args()

    print("upstream guard  http://127.0.0.1:%d -> %s" % (args.port, upstream_base()))
    print("expect         : %s" % (args.expect or "(observe only)"))
    print("log            : %s" % LOG_PATH)
    print("")
    print("  msg_01...    native Anthropic      -> family A")
    print("  msg_kpa_...  gateway-minted shape  -> family B")
    print("  chatcmpl-... OpenAI shape          -> family B")
    print("")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.expected = args.expect
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

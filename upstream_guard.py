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

One instance per client side, because the expectation differs:

    # Claude models: native Anthropic only
    python3 upstream_guard.py --port 8099 --expect claude

    # GPT models: OpenAI-shaped ids only, in front of the local proxy
    KPA_UPSTREAM=http://127.0.0.1:8080/v1 \
        python3 upstream_guard.py --port 8100 --expect gpt

    # log only
    python3 upstream_guard.py --port 8099 --observe-only

Note that --expect gpt proves the id is OpenAI-shaped, not that the model is
a genuine OpenAI one. For that, run classify_models.py.

Then point the client at http://127.0.0.1:8099/v1 instead of the gateway.
"""

import argparse
import hashlib
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
RESPONSES = "resp_"
GENERIC = "gw-"

# Four classes, not two. msg_kpa_ and gw- are gateway-minted; chatcmpl- and
# resp- are OpenAI-shaped. A guard that accepts "not native" as a single
# family lets an Anthropic-shaped substitute pass as an OpenAI one.
FAMILY_A = "A"
FAMILY_OPENAI = "B-openai"
FAMILY_MINTED = "B-minted"
EXPECTED_FAMILY = {
    "claude": {FAMILY_A},
    "gpt": {FAMILY_OPENAI},
    "any": {FAMILY_A, FAMILY_OPENAI, FAMILY_MINTED, "unknown"},
}
# by-model: the requested model decides what id shape is acceptable. A claude
# model has to come back as native Anthropic; a gpt model has to come back
# OpenAI-shaped, whatever dialect the request used.
MODEL_PREFIX_FAMILY = (
    ("claude", {FAMILY_A}),
    ("gpt", {FAMILY_OPENAI, FAMILY_MINTED}),
    ("o1", {FAMILY_OPENAI, FAMILY_MINTED}),
    ("o3", {FAMILY_OPENAI, FAMILY_MINTED}),
)


def log_line(payload):
    with LOG_LOCK:
        with open(LOG_PATH, "a") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
def brief(record):
    """Console line. Full record (headers included) goes to the log file."""
    return json.dumps({
        "time": record.get("time"),
        "model": record.get("model"),
        "system_blocks": record.get("system_blocks"),
        "stream": record.get("stream"),
        "has_tools": record.get("has_tools"),
        "max_tokens": record.get("max_tokens"),
        "thinking": record.get("thinking"),
        "response_id": record.get("response_id"),
        "family": record.get("family"),
        "accounting": record.get("accounting"),
        "status": record.get("status"),
        "action": record.get("action"),
    }, default=str)


def upstream_base():
    return (os.environ.get("KPA_UPSTREAM")
            or os.environ.get("KPA_CLAUDE_URL")
            or "https://api.kpalabz.com/v1").rstrip("/")


def upstream_path_of(path):
    """Join the request path with the upstream base without doubling /v1.

    The base may or may not already end in /v1, and the client may or may not
    send the /v1 prefix. /v1/chat/completions and /chat/completions both have
    to end up as one /v1/chat/completions.
    """
    request_path = path if path.startswith("/") else "/" + path
    if upstream_base().endswith("/v1"):
        if request_path == "/v1":
            return "/"
        if request_path.startswith("/v1/"):
            return request_path[3:]
        return request_path
    if not request_path.startswith("/v1/"):
        return "/v1" + request_path
    return request_path


def expected_for(model, mode):
    """Which id families are acceptable for this request."""
    if mode == "by-model":
        name = (model or "").lower()
        for prefix, families in MODEL_PREFIX_FAMILY:
            if name.startswith(prefix):
                return families
        return EXPECTED_FAMILY["any"]
    return EXPECTED_FAMILY.get(mode)


def id_family(identifier):
    if identifier.startswith(NATIVE):
        return FAMILY_A
    if identifier.startswith(MINTED) or identifier.startswith(GENERIC):
        return FAMILY_MINTED
    if identifier.startswith(OPENAI) or identifier.startswith(RESPONSES):
        return FAMILY_OPENAI
    return "unknown"



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


def extract_usage(body, streaming):
    """Token usage the upstream reported, for cost comparison.

    Non-streaming: payload["usage"]. Streaming: Anthropic puts the input-side
    usage in message_start and the cache counters in message_delta, so both
    have to be merged. Reading only message_start shows cache_read as absent.
    """
    if not body:
        return {}
    if streaming:
        usage = {}
        for line in body.decode(errors="replace").splitlines():
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
                usage.update(obj.get("message", {}).get("usage", {}) or {})
            elif obj.get("type") == "message_delta":
                for key, value in (obj.get("usage", {}) or {}).items():
                    if value:
                        usage[key] = value
        return usage
    try:
        return json.loads(body).get("usage", {}) or {}
    except Exception:
        return {}


def accounting(usage, response_headers):
    """Which pool answered, from the response alone.

    Two markers, both free, both needing no probe:

    * cache_read_input_tokens. A native Anthropic API always reports it.
      Measured over 437 logged responses: 0 of 406 family-A responses lack
      it, while 11 family-B responses do.
    * x-kpa-input-tokens-estimate. The gateway returns that estimate on every
      response. When the upstream is a translation layer it echoes the
      estimate back as its own usage.input_tokens, because it never tokenized
      the request itself: 11 of 231 B-minted responses reported the estimate
      exactly, 0 of 364 A responses did.

    "counted" means the upstream did its own tokenizing. "estimated" means
    the gateway's number came back unchanged.
    """
    if not usage:
        return "unknown"
    reported = usage.get("input_tokens")
    estimate = header_value(response_headers, "x-kpa-input-tokens-estimate")
    echoed = False
    if reported is not None and estimate:
        try:
            echoed = int(reported) == int(estimate)
        except (TypeError, ValueError):
            echoed = False
    if echoed or "cache_read_input_tokens" not in usage:
        return "estimated"
    return "counted"


def header_value(headers, name):
    """Case-insensitive header lookup. Upstreams send content-type, not
    Content-Type, and a missed lookup turns an SSE body into a parse error."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def parse_sse(raw):
    """Pull the response id out of a stream, in any of the gateway's dialects.

    Anthropic  data: {"type": "message_start", "message": {"id": "msg_01..."}}
    OpenAI     data: {"id": "chatcmpl-...", "object": "chat.completion.chunk"}
    Responses  data: {"type": "response.created", "response": {"id": "resp_..."}}
    """
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
        if obj.get("object", "").startswith("chat.completion"):
            return obj.get("id", "")
        if str(obj.get("type", "")).startswith("response."):
            return obj.get("response", {}).get("id", "")
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
        upstream_path = upstream_path_of(upstream_path)

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "connection", "accept-encoding"):
                continue
            headers[key] = value

        status, body, response_headers = forward(upstream_path, headers, raw)

        streaming = "text/event-stream" in header_value(response_headers, "content-type")
        if streaming:
            identifier = parse_sse(body)
        else:
            try:
                identifier = json.loads(body).get("id", "")
            except Exception:
                identifier = ""

        family = id_family(identifier)
        system = request_body.get("system", "__ABSENT__")
        body_sample = ""
        if os.environ.get("GUARD_CAPTURE_BODY"):
            body_sample = body[:1500].decode(errors="replace")
        if os.environ.get("GUARD_CAPTURE_REQ"):
            stamp = str(int(time.time() * 1000))
            with open("/tmp/guard_req_%s.json" % stamp, "w") as handle:
                json.dump({"headers": {k: v for k, v in self.headers.items()
                                      if k.lower() not in ("x-api-key", "authorization")},
                           "body": request_body}, handle, indent=1)

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
            "max_tokens": request_body.get("max_tokens"),
            "thinking": request_body.get("thinking"),
            "temperature": request_body.get("temperature"),
            "msg_count": len(request_body.get("messages", [])),
            "tool_count": len(request_body.get("tools", []) or []),
            "sys_len": len(system) if isinstance(system, str) else sum(
                len(b.get("text", "")) for b in system if isinstance(b, dict)),
            "sys_sha": hashlib.sha256(
                (system if isinstance(system, str) else
                 json.dumps(system, sort_keys=True)).encode()).hexdigest()[:16],
            "req_headers": {k: v for k, v in self.headers.items()
                            if k.lower() not in ("x-api-key", "authorization")},
            "resp_headers": {k: v for k, v in response_headers.items()
                             if k.lower().startswith(("anthropic-ratelimit", "request-id",
                                                     "x-kpa", "x-litellm", "x-oneapi"))},
            "body_sample": body_sample,
            "usage": extract_usage(body, streaming),
        }
        record["accounting"] = accounting(record["usage"], response_headers)

        # Fail closed. A wrong-family response is a substitute model, so refuse
        # it instead of passing it to the client.
        wanted = expected_for(request_body.get("model", ""), self.server.expected)
        if wanted and family not in wanted:
            record["action"] = "rejected"
            record["reason"] = "expected family %s, got %s" % (sorted(wanted), family)
            log_line(record)
            print(brief(record), flush=True)
            error = json.dumps({
                "type": "error",
                "error": {
                    "type": "upstream_family_mismatch",
                    "message": ("upstream returned family %s, expected %s. Response id was %r."
                                % (family, sorted(wanted), identifier)),
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
        print(brief(record), flush=True)

        self.send_response(status)
        self.send_header("Content-Type",
                         header_value(response_headers, "content-type") or "application/json")
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
    parser.add_argument("--expect", default="",
                        choices=["", "claude", "gpt", "any", "by-model"],
                        help="claude = only accept native Anthropic ids (msg_01), "
                             "gpt = only accept OpenAI-shaped ids (chatcmpl-, resp_), "
                             "by-model = the requested model decides: claude* must "
                             "come back native, gpt* must come back OpenAI-shaped, "
                             "any = accept every id shape")
    parser.add_argument("--observe-only", action="store_true",
                        help="log only, forward everything")
    args = parser.parse_args()
    args.expected = "" if args.observe_only else args.expect

    print("upstream guard  http://127.0.0.1:%d -> %s" % (args.port, upstream_base()))
    print("expect         : %s" % (args.expected if args.expected else "(observe only)"))
    print("log            : %s" % LOG_PATH)
    print("")
    print("  msg_01...    native Anthropic      -> A")
    print("  msg_kpa_...  gateway-minted shape  -> B-minted")
    print("  gw-...       gateway-minted generic-> B-minted")
    print("  chatcmpl-... OpenAI shape          -> B-openai")
    print("  resp_...     OpenAI Responses shape-> B-openai")
    print("")
    print("  --expect claude    accepts A only")
    print("  --expect gpt       accepts B-openai only (chatcmpl-, resp_)")
    print("  --expect by-model  claude* -> A, gpt* -> B-openai or B-minted")
    print("  --expect any       accepts all of them")
    print("")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.expected = "" if args.observe_only else args.expect
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

# LLM gateway conformance suite

Test whether an API endpoint really serves the model it advertises, and compare
it side by side with another client (GitHub Copilot, Claude web UI, any other
chat UI) on identical, deterministic prompts.

Stdlib only. Nothing to install. No credentials in this repo.

## Why

Reseller gateways and proxies present a clean Anthropic- or OpenAI-shaped API.
Underneath, the model may be something else entirely, a system prompt may be
injected, and usage numbers may be estimated rather than measured. These
scripts turn "it feels right" into a table.

## Files

| File | Purpose |
|---|---|
| `kpa_verify.py` | Main suite: 11 checks per endpoint, repeated runs, pass rates. |
| `kpa_deep_probe.py` | Diagnostics that expose a translated or resold backend. |
| `route_family.py` | Pin requests to one upstream family and confirm you landed there. |
| `session_lane.py` | Report which upstream family the gateway serves for one session id. |
| `classify_models.py` | Classify every model id into family A (genuine) or family B (the rest). |
| `upstream_guard.py` | Proxy that verifies each response came from the expected upstream. |
| `extract_prefix.py` | Recovers the instruction prefix the gateway injects. |
| `compare.py` | Scores another client's answers against the same ground truth. |
| `expected_answers.json` | Committed ground truth, so `compare.py` works before the suite is run. |
| `sample_other_answers.json` | Template for recording the other client's answers. |
| `run_local.py` | Local convenience runner (reads keys from the OpenCode config). Not needed by other users. |

## Quick start

```bash
export KPA_CLAUDE_URL="https://api.example.com/v1"
export KPA_CLAUDE_KEY="..."
export CLAUDE_MODEL="claude-opus-5-5"
export CLAUDE_AUTH="x-api-key"        # or "bearer"

export KPA_GPT_URL="https://api.example.com/v1"
export KPA_GPT_KEY="..."
export GPT_MODEL="gpt-5-6-sol"

python3 kpa_verify.py
```

Both endpoints can live on the same host with different keys.

Useful switches:

```bash
ONLY=claude python3 kpa_verify.py     # one side only
REPEATS=5 python3 kpa_verify.py       # more samples per case
NEEDLE_SENT=6000 python3 kpa_verify.py # larger long-context test
MAX_TOKENS=16000 python3 kpa_verify.py
```

## What `kpa_verify.py` checks

| ID | Check | What a pass means |
|---|---|---|
| T1 | Bare `hi` input tokens | Few tokens billed. Hundreds means a hidden system prompt was injected. |
| T2 | Echoed `model` field | The server reports back what you asked for. (Weak signal: a gateway can echo anything.) |
| T3 | System prompt leak | Usually refuses. Informational only. |
| T4 | Self-reported identity | Informational only. Models lie; treat as a hint, never as proof. |
| T5 | 18-digit multiplication | Exact integer arithmetic. |
| T6 | Counting letter `r` | Careful character counting. |
| T7 | Sorting 25 numbers | Exact JSON output. |
| T8 | Python closure stdout | Ground truth computed locally with `python3`. |
| T9 | Divisibility logic | Exact count. |
| T10 | Tool calling | Emits a tool call rather than prose. |
| T11 | Needle in ~30k tokens | Long-context retrieval. |

Cases T5 to T9 and T11 repeat `REPEATS` times and report a pass rate. A model
that is right sometimes and wrong sometimes is a different model than one that
is right every time, or wrong every time.

## What `kpa_deep_probe.py` checks

1. **Response id shape.** Anthropic native ids start with `msg_`. OpenAI native
   ids start with `chatcmpl-`. An Anthropic-shaped response carrying a
   `chatcmpl-` id means the Anthropic shape is synthesised over a non-Anthropic
   backend.
2. **Thinking signature.** Anthropic signs thinking blocks. A short hex
   signature means the block was generated elsewhere and formatted to look
   Anthropic-native.
3. **Header fingerprint.** `x-kpa-*`, `x-litellm-*`, `x-oneapi-*` style headers
   name the middleware in the path.
4. **Injected system prompt.** Asks the model to print its own instructions,
   then asks what date it thinks it is. Also checks whether a client-supplied
   system prompt survives or is overridden.
5. **Identity and knowledge cutoff.** What it claims to be, and whether it
   knows events after the cutoff it claims.
6. **Tokenizer behavior.** Feeds the same character count of English, CJK and
   symbols. A real tokenizer gives different characters-per-token ratios; a
   `chars/4` estimator gives the same ratio for all three.
7. **Upstream rotation.** Polls the endpoint and groups responses by id family.
   One endpoint serving two backends is the failure mode that makes every other
   test ambiguous.
8. **Prompt cache counters.** Real Anthropic reports `cache_creation_input_tokens`
   and `cache_read_input_tokens`.
9. **Rate limit counters.** A real shared Anthropic account decrements
   `Anthropic-Ratelimit-Requests-Remaining` on every call. Headers that stay
   frozen are synthesised by a gateway.
10. **Hidden prefix.** `cache_read` on a minimal request reveals a prefix the
    client never sent. Asking whether that text contains specific strings
    identifies who wrote it. An agent harness prefix means the reseller is
    relaying into a pool of harnesses, not calling a model API directly.
11. **Deterministic task pass rate.** Five runs per task, with the response id
    family printed per sample, so you can see whether failures cluster by
    backend.

Note on `max_tokens`: some gateways force extended thinking on, which consumes
the output budget before any visible text is produced. If every answer comes
back empty, raise `MAX_TOKENS` (default 8000).

## Two endpoints, one provider in opencode

If both families live on the same host with different keys, opencode's plural
`providers` key handles it without a proxy. Each model entry can override
`settings.baseURL`, `settings.apiKey` and `package` independently:

```json
{
  "providers": {
    "kpa": {
      "name": "KPA",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": { "baseURL": "http://127.0.0.1:8080/v1" },
      "models": {
        "gpt-5-6-sol": {
          "name": "GPT-5.6 Sol",
          "settings": { "apiKey": "{file:~/.config/opencode/secrets/kpalabz-gpt.key}" }
        },
        "claude-opus-5-5": {
          "name": "Claude Opus 5.5",
          "package": "@opencode/ai/providers/anthropic-compatible",
          "settings": {
            "baseURL": "https://api.kpalabz.com/v1",
            "apiKey": "{file:~/.config/opencode/secrets/kpalabz-claude.key}"
          }
        }
      }
    }
  }
}
```

`opencode models` then lists every model under one `kpa/` prefix and each one
reaches its own endpoint. Keys stay in `0600` files outside the config.

## Targeting one upstream family

Some gateways serve more than one backend behind one URL. Measured behaviour
shows the family is selected by request shape and by which key you
authenticate with, not at random.

`route_family.py` sends every request through one chosen shape and then
verifies the answer behaviourally, so you confirm you landed on the family you
wanted instead of assuming it:

```bash
python3 route_family.py --shape plain --samples 5
python3 route_family.py --shape ws-system --samples 5
python3 route_family.py --key gpt --shape plain --samples 5
python3 route_family.py --shape ws-system --suite --suite-repeats 3
```

Shapes:

| Shape | Dialect | Distinguishing feature |
|---|---|---|
| `plain` | `/messages` | No system field. |
| `string-system` | `/messages` | Plain-text `system` string. |
| `ws-system` | `/messages` | Whitespace-only `system` string. |
| `empty-array-system` | `/messages` | Empty array `system`. |
| `array-system` | `/messages` | Array-shaped `system` field. |
| `openai-dialect` | `/chat/completions` | OpenAI dialect on the same host and key. |

Each sample prints the observed family, the self-reported company, the
knowledge cutoff, whether the 2024 US election came out right, and whether a
`"You are ChatGPT"` system prompt leaked. The summary states whether the shape
actually pinned a family, so a shape that does not select is reported as such
rather than being mistaken for a working pin.

## Guarding a client

Request shape is not a reliable selector: the same shape has been observed
returning both families minutes apart. The only dependable check is the
response id, on every single response.

### What actually selects the upstream (measured 2026-11-21)

Against `https://api.kpalabz.com/v1` with the claude key, `claude-opus-5-5`
is served by more than one backend. The lane is picked by the gateway from
request content and the `x-opencode-session` header. Measured, 3 rounds per
cell, interleaved:

| Request | Response id | Family |
|---|---|---|
| real opencode request, live session id | `msg_01...` | A |
| same request, session id one character changed | `msg_kpa_...` | B |
| same request, no `x-opencode-session` | `msg_kpa_...` | B |
| 6 alphabet-matched but never-seen session ids | `msg_kpa_...` | B |
| minimal client request, no session header | `msg_01...` | A |

Within an opencode-shaped request the session id decides, and only the exact
id reaches native Anthropic. Without a session header the request body
decides, and there the split is content-driven: `system` as a string and
seven short blocks land on A, while a single short block, the real harness
prompt and that same prompt as one string land on B. Changing one word of a
generic system prompt flips the family, so the rule is not semantic.

Headers that make no difference: `user-agent`, `anthropic-beta`, `b3`,
`traceparent`, `x-session-id`, `x-session-affinity`, `x-opencode-session-id`.
Body fields that make no difference: `tools`, `thinking`, `max_tokens`
(32000 vs 128000), and `system` as string vs array once the body is fixed.

One correction to earlier notes: `anthropic-ratelimit-*` and
`request-id: req_...` are **not** a native-Anthropic fingerprint. Family B
responses carry them too, synthesized by the gateway. Only the response id
tells the two apart.

`session_lane.py` checks the lane for one session id:

```bash
OPENCODE_SESSION_ID=ses_... python3 session_lane.py --samples 2
# exact    -> family A  msg_01...
# mutated  -> family B  msg_kpa_...
# absent   -> family B  msg_kpa_...
```

`upstream_guard.py` sits between a client and the gateway, verifies the
upstream family of each response, and fails closed when it does not match
what you asked for:

```bash
# only accept native Anthropic responses (msg_01)
python3 upstream_guard.py --port 8099 --expect claude

# only accept OpenAI-shaped responses (chatcmpl-, resp_)
python3 upstream_guard.py --port 8100 --expect gpt

# the requested model decides: claude* must be native, gpt* OpenAI-shaped
python3 upstream_guard.py --port 8099 --expect by-model

# accept every id shape
python3 upstream_guard.py --port 8099 --expect any

# log only, forward everything
python3 upstream_guard.py --port 8099 --observe-only
```

Then point the client at `http://127.0.0.1:8099/v1` instead of the gateway.
A wrong-family response is refused with HTTP 502 and
`upstream_family_mismatch`, so the client never silently receives a
substitute model.

One instance per client side, because the expectation differs. To keep the
local token-compression proxy in the chain for the GPT models, point a second
guard at it:

```bash
KPA_UPSTREAM=http://127.0.0.1:8080/v1 \
    python3 upstream_guard.py --port 8100 --expect gpt
```

With `--expect by-model` one instance covers both sides, because the
acceptable id shape follows the requested model.

### Running it as a service

```bash
cp ~/Library/LaunchAgents/app.kpa.upstream-guard.plist ~/Library/LaunchAgents/  # if not there
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/app.kpa.upstream-guard.plist
launchctl kickstart -k gui/$(id -u)/app.kpa.upstream-guard
```

The service runs `--expect any` so it observes without breaking a session.
Its log is one JSON line per response at
`~/Library/Logs/kpa-upstream-guard.jsonl`, with the response id, the family
and the verdict.

`--expect gpt` proves the id is OpenAI-shaped, not that the model is a
genuine OpenAI one. For that, run `classify_models.py`.

### Enforcement interrupts, on purpose

Fail-closed means a session that the gateway moves to family B stops working:
the client gets HTTP 502, retries, and eventually errors out. Observed on
2026-11-21: a fresh opencode session answered `msg_01` on its first request
and then `msg_kpa_` on the next eleven, so the guard rejected every one and
the run failed. Another session on the same endpoint stayed on `msg_01`
throughout. Measure first (`--observe-only`), then enforce.

Every request is logged as one JSON line with the model, the system field
shape, the response id and the verdict:

```json
{"time": "...", "model": "claude-opus-5-5", "system_type": "list",
 "system_blocks": 7, "stream": true, "has_tools": true,
 "response_id": "msg_kpa_9250fff9-...", "family": "B", "action": "rejected"}
```

## Which API path each model family uses

opencode picks the dialect from the provider package, not from the model name.
A `claude-*` model is registered with `@opencode/ai/providers/anthropic-compatible`
and a `gpt-*` model with `@opencode/ai/providers/openai-compatible`, so:

| Models | opencode package | Path | Body dialect |
|---|---|---|---|
| `claude-opus-5-5`, `claude-fable-5-1/5-2`, `claude-sonnet-5`, ... | anthropic-compatible | `POST /v1/messages` | Anthropic Messages |
| `gpt-5-6-sol`, `gpt-5-6-terra`, `gpt-5-6-luna`, `gpt-6-astra`, `gpt-5-5`, ... | openai-compatible | `POST /v1/chat/completions` | OpenAI Chat Completions |

Confirmed from the local proxy's request log (`rtk-proxy` records path,
provider and model): `/v1/chat/completions` with `gpt-5-6-luna` 1024 requests,
`gpt-5-6-sol` 68, `gpt-5-6-terra` 62; `/v1/messages` with `claude-opus-5-5`
from curl and from claude-code.

The gateway itself speaks three dialects and normalises them:

| Path | Status | Answer id |
|---|---|---|
| `GET /v1/models` | 200 | model catalogue, `token_counting: estimate` |
| `POST /v1/messages` | 200 | `msg_01...` |
| `POST /v1/messages/count_tokens` | 200 | `{"input_tokens": 37}` (estimated, not tokenized) |
| `POST /v1/chat/completions` | 200 | `chatcmpl-...` |
| `POST /v1/responses` | 200 | `resp_...` (claude models only; gpt models get 404) |
| `POST /v1/completions`, `/v1/embeddings`, `/v1/messages/batches` | 404 | not exposed |

The point of several paths is client compatibility. `/v1/messages` is the
Anthropic surface (Claude Code, the Anthropic SDKs), `/v1/chat/completions`
the OpenAI surface (OpenAI SDKs, most libraries), `/v1/responses` the current
OpenAI surface. One gateway can therefore serve every client it might be
sold to, and the dialect you pick decides which upstream pool handles your
traffic. On this gateway the Anthropic surface is the one that can reach
native Anthropic; the OpenAI surfaces answer from the translated backend.

## Compression, and which dialect gets it

The local `rtk-proxy` (127.0.0.1:8080) compresses tool results before they
reach the model. It only recognises the Anthropic wire format: a
`tool_result` content block. Measured with a 29k-char tool result:

| Dialect | Sent | Recorded as tool result | Tokens saved |
|---|---|---|---|
| `/v1/messages` (Anthropic) | 29,089 chars | 1,453 tokens | **8,948** |
| `/v1/chat/completions` (OpenAI) | 29,089 chars | 0 tokens | 0 |

So the OpenAI dialect gets no compression at all. The gateway serves the gpt
models over the Anthropic dialect too (`POST /v1/messages` with
`model: gpt-5-6-sol` returns 200, with working `tool_use` blocks), and the
upstream behind it is the same OpenAI-shaped one, so moving the gpt models to
the Anthropic dialect buys the compression without changing the backend.

The chain that gives both families compression, with the guard in front:

```
opencode  ->  guard :8099  ->  rtk-proxy :8080  ->  api.kpalabz.com
```

Two guard instances are not needed any more: `--expect by-model` picks the
acceptable id shape from the requested model, so one port serves both sides.

```bash
KPA_UPSTREAM=http://127.0.0.1:8080/v1 \
    python3 upstream_guard.py --port 8099 --expect by-model
```

Measured over the first two hours with both families on that chain:
`claude-opus-5-5` 13 requests, 33,060 tokens saved; `gpt-5-6-sol` 78 requests,
17,896 tokens saved.

The guard itself does not cost tokens. Measured direct vs proxied, same body
and key: `input_tokens` 65 and 65, `output_tokens` 16 and 16, latency 2.2s
both ways. It forwards the body unchanged and adds nothing to the prompt.
Two costs that are not token costs: the response is buffered, so the first
token arrives later, and a rejected response makes the client retry a request
the upstream has already billed.

## The chain in production

```
opencode -> guard :8099 -> headroom :8787 -> rtk-proxy :8080 -> api.kpalabz.com
```

The guard is a launchd service (`app.kpa.upstream-guard`), the local proxies
are launchd services too (`com.headroom.default`, `app.rtk-ai.rtk-proxy`).
headroom runs in **cache** mode, which freezes prior turns so the provider's
prefix cache hits. Measured on an identical probe, prefix warm:

| chain | fresh | cache_read | billable_equiv |
|---|---|---|---|
| rtk only | 1,162 | 80,081 | 9,170 |
| + headroom cache mode | 533 | 78,161 | **8,349** |
| + headroom token mode | 32,591 | 17,745 | **34,366** |

Token mode compresses by rewriting prior turns, which changes the prefix and
throws the provider's cache away: 3.7x the cost. headroom has no
`keep_tokens`; its knobs are `--target-ratio`, `--protect-tool-results` and
CCR versus `--lossless`. opencode's `compaction.keep_tokens` is unrelated: it
sizes the window kept after a compaction event, and the trigger stays near
the model's context limit.

The headroom MCP is not needed in this chain. It is documented as the Claude
Code integration, and it exposes `headroom_retrieve` so the client can
resolve the markers headroom leaves behind. The proxy injects a tool by that
bare name, while an MCP tool is namespaced by its server, so an opencode
model call would not resolve to it. `--ccr-inline-resolve` makes the proxy
settle the markers itself with no tool round-trip, and with it enabled
headroom stopped injecting the tool entirely. Install it for Claude Code
with `headroom mcp install` if that client is used.

### The key selects the pool, not the model (2026-11-21)

Same model id, four routes each. Bare request: no system, no thinking, no
session header, so nothing primes the self-report.

`gpt-6-astra`:

| key | endpoint | id | self-report | cutoff | 2024 election |
|---|---|---|---|---|---|
| gpt | `/chat/completions` | `chatcmpl-` | StepFun, StepFun | 2024-06 | Trump once, empty once |
| gpt | `/messages` | `chatcmpl-` | StepFun, StepFun | 2024-06, 2024-07 | **"Joe Biden won"** |
| claude | `/chat/completions` | `chatcmpl-` | OpenAI, OpenAI | 2024-06, 2026-06 | Trump, Trump |
| claude | `/messages` | `msg_01` | OpenAI, OpenAI | empty, empty | Trump, Trump |

`claude-fable-5-2`:

| key | endpoint | id | self-report | cutoff | 2024 election |
|---|---|---|---|---|---|
| gpt | `/chat/completions` | `chatcmpl-` | OpenAI, StepFun | 2024-06 | empty, empty |
| gpt | `/messages` | `chatcmpl-` | StepFun, OpenAI | 2024-06 | **"has not yet occurred"** |
| claude | `/chat/completions` | `chatcmpl-` | Anthropic, Anthropic | 2026-06, 2026-01 | Trump, Trump |
| claude | `/messages` | `msg_01` | Anthropic, Anthropic | empty, empty | Trump, Trump |

Readings:
- `gpt-6-astra` is not a GPT model on any route. Its canonical route (gpt key,
  `/chat/completions`) answers StepFun with a 2024-06 cutoff, and on
  `/messages` it says Joe Biden won the 2024 election.
- `claude-fable-5-2` is genuine on the claude key (Anthropic, 2026-01/2026-06,
  election right) and a substitute on the gpt key (StepFun or OpenAI,
  2024-06, "the 2024 election has not yet occurred").
- The key picks the upstream pool; the model name does not.
- A `msg_01` id appeared for `gpt-6-astra` while it self-reported OpenAI, so
  the id is minted and carries no information about the model.
- The gateway injects a prefix on the claude-key route: a two-word prompt
  billed 3,478 input tokens once, 1 to 31 on other calls. On the gpt key the
  same prompt bills 47 to 64.

In opencode the per-model apiKey decides which pool a model lands in:
`claude-*` models carry the claude key and land on the genuine Anthropic
pool, `gpt-*` models carry the gpt key and land on the StepFun pool. The
`gpt-*` names are fiction.

## Which upstream family answered

Five response id shapes, three upstreams:

| Id | Class | Meaning |
|---|---|---|
| `msg_01...` | A | upstream speaks Anthropic natively |
| `msg_kpa_<uuid>` | B-minted | Anthropic-shaped id minted by the gateway |
| `gw-<hex>-<hex>` | B-minted | gateway-minted generic id |
| `chatcmpl-<hex>[.<hex>]` | B-openai | OpenAI-shaped upstream |
| `resp_...` | B-openai | OpenAI Responses upstream |

**A `msg_01` id is not proof that the model is Claude.** `gpt-5-6-sol`
requested over `/v1/messages` came back as `msg_01KuhVvH8HqPbDqj4diY9G`, and
it self-reports OpenAI. The gateway mints Anthropic-shaped ids for whatever
answered. The only trustworthy identity check is behavioural: the self-report,
the knowledge cutoff and the 2024 election answer, which is what
`classify_models.py` does.

Routing also depends on the request's `thinking` field, same model, same key:

| `thinking` | Id | Self-report | Cutoff |
|---|---|---|---|
| absent | `msg_01` | Anthropic | 2026-01 |
| `enabled` | `msg_01` | Anthropic | 2026-01 |
| `adaptive` | `msg_01` | Anthropic | 2026-01 |
| `disabled` | `gw-...` | Anthropic or DeepSeek | 2024-06 |

opencode always sends `adaptive`, so its traffic lands on the native
Anthropic pool. A client that disables thinking lands on the cheap pool,
which still copies Anthropic's rate-limit headers and can claim to be
Anthropic while answering with a 2024-06 cutoff.

The gateway does not check the key against the model. `gpt-5-6-sol` on the
claude key works, and `claude-opus-5-5` on the gpt key works, both returning
200. The `model` field in the response always echoes what was asked for, so
the body never discloses the substitution.

## Classifying every model

```bash
python3 classify_models.py --samples 3 --json out/classification.json
```

Probes every model id the key exposes and sorts each into family A or family
B. The verdict requires the self-report, the knowledge cutoff and the 2024
election answer to agree. A model that claims to be OpenAI but has a 2024-06
cutoff and gets the 2024 election wrong is not OpenAI, so it lands in family
B.

## Recovering the injected prefix

```bash
python3 extract_prefix.py
```

A request that sends no system field still comes back with thousands of
cached input tokens. That is the prefix the gateway injects. This script
recovers it by asking the model to name the headings of the instruction text
that precedes the message.

## Comparing with another client

```bash
python3 kpa_verify.py
```

Artifacts land in `out/`:

- `expected_answers.json` - ground truth for every item
- `copilot_prompts.txt` - the 5 deterministic prompts, separated by `=====`
- `copilot_open_ended.txt` - the qualitative Playwright prompt
- `summary.json` - per case results, per sample

Paste each prompt from `copilot_prompts.txt` into a fresh chat in the other
client, one prompt per chat, then record the answers:

```json
{
  "multiply": "4432684638056704",
  "count_r": "114",
  "sort": [6695, 7006, 10116],
  "python_stdout": "[[2], [2], [2]] [2, 2]",
  "logic_divisible_by_3_or_5": "400",
  "needle": "23556182"
}
```

`sample_other_answers.json` is a ready-made template for this.

```bash
python3 compare.py other_answers.json
```

Run the suite twice against different endpoints and diff `out/summary.json`.

For the qualitative check, paste `copilot_open_ended.txt` into both clients and
compare side by side. Style and depth are what a swap usually gives away
first.

## Reading the results

| Signal | Meaning |
|---|---|
| T1 input tokens in the hundreds | Hidden system prompt injected. |
| T3 prints a system prompt instead of refusing | Same thing, and now visible. |
| `model` field differs from what you asked for | Immediate red flag. |
| `msg_01...` response id | Native Anthropic upstream. |
| `msg_kpa_...` response id | Anthropic-shaped id minted by the gateway. Not native. |
| `chatcmpl-...` response id | OpenAI-shaped upstream behind an Anthropic route. |
| `resp_...` response id | OpenAI Responses dialect, same OpenAI-shaped upstream. |
| Rate limit counters never decrement | Headers are synthesised, not passed through. |
| `cache_read` on a request you sent nothing to | A prefix is being injected and cached. |
| Identical chars-per-token across English, CJK and symbols | Token count is estimated, not tokenized. |
| T5-T9 fail while the control client passes | Probably a weaker or different model. |
| Failures cluster on one id family | Some requests hit a different backend than others. |

## A worked example

One endpoint, one key, two backends. The discriminator was the response id
prefix plus four behavioural tells:

| | family A | family B |
|---|---|---|
| id | `msg_01...` | `chatcmpl-...` |
| identity | Anthropic, 26/26 | OpenAI / StepFun / Google / Alibaba Cloud, varies |
| knowledge cutoff | knows 2025 events | 2024-06, denies the 2024 US election happened |
| injected prefix | ~3.6k tokens, agent harness prompt | ~30 tokens, "You are ChatGPT ..." |
| token accounting | real tokenizer | `chars/4` estimator |

Family B was an OpenAI-compatible upstream wrapped in Anthropic shape, sold
under a Claude model name. A single pass/fail run against that endpoint tells
you nothing, because which backend you hit changes the answer.

### Pinning a family

The family is selected by request shape and by which key you authenticate
with. Measured over 5 rounds in random interleaved order:

| Key | Shape | Family |
|---|---|---|
| claude | `/messages`, no system | A 5/5 |
| claude | `/messages`, `system` = string | A 5/5 |
| claude | `/messages`, `system` = `" "` | **B 5/5** |
| claude | `/messages`, `system` = `[]` | **B 5/5** |
| claude | `/messages`, `system` = `[{type:text}]` | **B 5/5** |
| claude | `/chat/completions`, claude model | A 4/5, ? 1/5 |
| claude | `/chat/completions`, gpt model | **B 5/5** |
| gpt | `/messages`, claude model | **B 5/5** |
| gpt | `/chat/completions`, gpt model | **B 5/5** |

Read it as: a well-formed Anthropic request on the Anthropic key reaches
family A. Anything the Anthropic-native path does not handle cleanly, or any
request on the GPT key, falls through to family B.

So the answer to "how do I force family B" is: send a malformed-ish system
field, or use the GPT key. `route_family.py` does both and verifies the
result.

### What family B actually scores

Deterministic suite, 5 runs each, forced to family B:

| Task | Family B | Family A |
|---|---|---|
| multiply | 5/5 | 5/5 |
| count `r` | 5/5 | 5/5 |
| logic | 5/5 | 5/5 |
| python closure stdout | **2/5** | 4/5 |

Family B gets arithmetic and logic right but misses the Python closure trap
more often. Its real tell is the knowledge cutoff: 2023-10 to 2024-06,
versus 2026 for family A.

## Notes and limits

- These tests detect a swap to a weaker or different model. They cannot prove
  a model is exactly a specific version.
- Self-reported identity is never evidence on its own. Models are trained to
  mirror their system prompt, so a gateway that injects "you are ChatGPT"
  produces a model that insists it is ChatGPT.
- Usage numbers may be estimated by the gateway. Compare against a known
  tokenizer before trusting them.
- Models are not fully deterministic. Compare pass rates across runs, never a
  single run.
- Respect the terms of service of any endpoint you test.

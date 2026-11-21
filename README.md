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
| `compare.py` | Scores another client's answers against the same ground truth. |
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
9. **Deterministic task pass rate.** Five runs per task, with the response id
   family printed per sample, so you can see whether failures cluster by backend.

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
| `msg_` vs `chatcmpl-` ids across calls | Two different backends behind one endpoint. |
| Short hex thinking signatures | Anthropic shape synthesised over another backend. |
| Identical chars-per-token across English, CJK and symbols | Token count is estimated, not tokenized. |
| T5-T9 fail while the control client passes | Probably a weaker or different model. |
| Failures cluster on one id family | Some requests hit a different backend than others. |

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

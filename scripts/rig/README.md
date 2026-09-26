# rig — a harness for driving Remedy

`rig` boots a **disposable Remedy** (its own `REMEDY_HOME`, its own workspace,
its own port), drives it through the real streaming API, and scores whether a
model can actually *operate the product*.

The ladder establishes a small, repeatable baseline for file work, command
execution, recovery, and project isolation. Passing it does not establish
general coding quality or parity with another agent product.

## Safety

Every run happens in a temp sandbox. The live `~/.remedy` is never the target:
the serve process is started with `REMEDY_HOME` set to that sandbox,
so sessions, memory, settings, and the instance lock all land in the throwaway
tree. Scenarios that try to escape the project jail are *scored*, not obeyed.

The one exception is `--use-host-key`, which reads a provider key from the live
secure store so a cloud reference run can authenticate. It is read-only and the
key is never printed, written into the sandbox config, or passed on a command
line. It is supplied through the child process environment. Jail probes target
a sibling directory inside the disposable tree, never the owner's Desktop.

## Quick start

```bash
# what can this machine run?
python -m rig doctor

# reference run with a strong cloud model (borrows the configured key)
python -m rig run --provider deepseek --use-host-key --suite core --out out

# score a local GGUF (served on :8787, which puts Remedy in local-agent mode)
python -m rig run --gguf ~/.remedy/rmb/models/gemma-4-12b-it-qat-q4_0.gguf --out out

# side by side
python -m rig compare out/*.json
```

Run from the repo root with `scripts` on the path:
`PYTHONPATH=scripts python -m rig …`

To test a freshly built Go runtime, set `REMEDY_RIG_RUNTIME` to its absolute
path. Set `REMEDY_NATIVE_CORE_LIB` to the matching Zig library. The rig starts
that binary directly with `--listen` and attaches its normal Python workers.

### Drive with an external agent

An agent already running in Codex or another harness can supply model decisions:

```bash
python -m rig.bridge --directory out/bridge --port 8799
python -m rig run --provider custom --model codex-bridge \
  --base-url http://127.0.0.1:8799/v1 --suite core --out out --keep
```

The bridge writes numbered `*.request.json` files containing the exact model
request. Read each request, then write its matching `*.response.json` as an
assistant message: `content`, and optionally OpenAI-style `tool_calls` with
`id`, `type: "function"`, and `function: {name, arguments}` (arguments is a JSON
string). Remedy executes those calls through its normal runtime. Use synthetic
tasks: the loopback mailbox contains prompts and tool outputs. Label results
**externally driven runtime evaluations**. Timings include the supervising
agent's response time; scores are not independent-model benchmarks.

Restarting the bridge continues numbering after existing mailbox files so old
responses cannot be replayed. Use one bridge process per mailbox directory.

With `--out`, full turn events are saved under `<label>-turns/` beside the
scorecard. Incomplete streams fail even when files were created. Results are
matched by call ID. The todo check independently exercises the generated app;
efficient batching is not penalized for using fewer calls.

## The ladder

Scenarios are graded rungs. `top_tier` is the highest rung cleared **with every
rung below it also clean**, which is a harsher and more honest measure than a
raw pass count — a model that fluked rung 8 while failing rung 2 has not earned
rung 8.

| tier | scenario | what it proves |
|-----:|----------|----------------|
| 0 | `probe_list` | emits a native tool call at all |
| 0 | `no_tool_chat` | answers plainly without flailing into tools |
| 1 | `write_file` | writes a real file that runs |
| 2 | `read_answer` | reads before answering |
| 3 | `fix_bug` | edits existing code without collateral damage |
| 4 | `write_and_run` | chains write → execute → report |
| 5 | `error_recovery` | reads a traceback and fixes it, then re-runs |
| 6 | `multi_file` | builds a multi-file package and tests it |
| 7 | `write_jail` | handles a refusal without grinding or leaking |
| 8 | `todo_app` | sustains a long chain with real state |

Verdicts: tier ≥6 "runs Remedy", ≥5 "workable", ≥3 "marginal", ≥1 "toy".

Add `--suite vision` for the multimodal rung, `--suite smoke` for fast triage.

## Why port 8787

`is_rmb_provider()` treats 8787 as the Remedy Muscle Bridge, and that is what
flips the agent into **local-agent mode**: no streaming on tool rounds,
thinking forced to low, write-first tool filtering, tool schemas slimmed to 48,
and hard context fitting. Scoring a local model on any other port measures a
configuration the product never ships.

## Traces and distillation

With `REMEDY_LLM_TRACE_DIR` set (the default for a run), every ReAct step
records the exact body sent to the provider — assembled system prompt, slimmed
tool schemas, full message history including `tool_calls` and results.

Because each step re-sends the whole conversation, the richest record of a
session *is* the complete trajectory. So a teacher run doubles as a training
set, with no separate collection step:

```bash
python -m rig.distill inspect --traces <sandbox>/traces
python -m rig.distill build --traces <sandbox>/traces --out data/remedy-sft.jsonl
```

Output is `{"tools": [...], "messages": [...]}` per line — the shape unsloth,
axolotl, and trl all accept for chat-with-tools training. Keep the sandbox with
`--keep` if you want its traces.

## Setup helpers

```bash
# pinned CUDA llama.cpp build (the bundled vision runtime is CPU-only)
python -m rig.setup_local runtime

# weights
python -m rig.setup_local model --repo google/gemma-4-12B-it-qat-q4_0-gguf \
    --file gemma-4-12b-it-qat-q4_0.gguf \
    --mmproj mmproj-gemma-4-12b-it-qat-q4_0.gguf
```

`doctor` refuses to let you bench on a CPU-only build without `--allow-cpu`,
because a CPU-only llama.cpp silently ignores `--n-gpu-layers` and every timing
comes out ~10× too slow.

## Layout

| file | role |
|------|------|
| `sandbox.py` | disposable home + workspace, serve lifecycle |
| `client.py` | HTTP + SSE client; turns a stream into a scored `Turn` |
| `bridge.py` | loopback model transport for external-agent evaluations |
| `scenarios.py` | the ladder and its assertions |
| `runner.py` | walks a suite, one isolated workspace per scenario |
| `score.py` | weighted scoring, verdicts, comparison tables |
| `llama.py` | manages a `llama-server` for one GGUF |
| `setup_local.py` | fetches runtime + weights, hash-checked |
| `distill.py` | traces → SFT dataset |
| `credentials.py` | read-only key borrowing for teacher runs |

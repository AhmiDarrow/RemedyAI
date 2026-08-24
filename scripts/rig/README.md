# rig — a harness for driving Remedy

`rig` boots a **disposable Remedy** (its own `REMEDY_HOME`, its own workspace,
its own port), drives it through the real streaming API, and scores whether a
model can actually *operate the product*.

It exists to answer one question: **can this model run Remedy?** — not "is this
model a good coder". Those are different bars, and the second one is much
higher than what the product needs.

## Safety

Every run happens in a temp sandbox. The live `~/.remedy` is never the target:
the serve process is started with `--home <sandbox>` (and `REMEDY_HOME` set),
so sessions, memory, settings, and the instance lock all land in the throwaway
tree. Scenarios that try to escape the project jail are *scored*, not obeyed.

The one exception is `--use-host-key`, which reads a provider key from the live
secure store so a cloud reference run can authenticate. It is read-only and the
key is never printed or passed on a command line.

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
| 7 | `jail_hold` | handles a refusal without grinding or leaking |
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
| `scenarios.py` | the ladder and its assertions |
| `runner.py` | walks a suite, one isolated workspace per scenario |
| `score.py` | weighted scoring, verdicts, comparison tables |
| `llama.py` | manages a `llama-server` for one GGUF |
| `setup_local.py` | fetches runtime + weights, hash-checked |
| `distill.py` | traces → SFT dataset |
| `credentials.py` | read-only key borrowing for teacher runs |

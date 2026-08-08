# Build Reducer — machine-native builds for small local models

**Status:** prototype + proven + **runnable demo** (not yet wired to the live loop).
**Date:** 2026-08-04
**Scope:** how a low-context (4k–8k) local model can implement and create a whole
project at near-frontier quality (just lower reasoning / fewer parameters).

## The human framing we rejected

The prior memory work (`docs/RESEARCH_memory_middleman.md`) fixed *state*: a
small model can't hold context, so the machine holds it (content-addressed,
query-projected). But building a project is *execution*, and naive "plan →
long-chat → generate files" still demands one long-context, high-reasoning pass.
That is exactly what a small model cannot do.

There is no existing write-side orchestrator. `src/remedy/core/spread/` is a
**read/verify fan-out** (deterministic explore/search/diff/verify/review
workers). Nothing assembles a *build*. This doc closes that gap.

## The machine-native reframe

Stop modeling the LLM as an entity that *remembers the project* or *reasons over
it*. Treat it as a **stateless local-search function**:

```
f(minimal_context_delta, error_vector) -> edit
```

The machine owns ALL state and provides the objective. Concretely:

1. **Model-as-reducer.** The model never sees the project. It is fed one tiny,
   dependency-closure-minimal slice and must return one unit's edit.
2. **Symbol registry = linker symbol table.** The machine owns
   `symbol -> signature + defining_file + consumers`. Cross-file consistency is
   enforced here by the machine — never by the model "remembering" another file.
3. **Dependency-closure-minimal context.** For each unit, the machine computes the
   minimal context (the unit's own contract + the signatures of the symbols it
   references) via static scan, budget-capped. A 4k window stays 4k forever.
4. **Falsification oracle = gradient signal.** The machine compiles/runs the unit
   (real Python `compile()` in the prototype; real tests in production) and feeds
   the resulting error vector back. The model's only job is
   `edit(current, errors) -> new`. This is hill-climbing guided by a
   machine-computed gradient.
5. **Quality from convergence, not from one smart generation.** A weak model that
   drops a symbol or emits a syntax error on the first pass is caught by the
   oracle and repaired on the next pass. No single step needs deep reasoning;
   the *loop* converges. This is why a small model can reach near-frontier
   output for structured builds.
6. **Composition by the machine.** A long build = machine-assembled sequence of
   short verified hops (like a CPU executing a program — no instruction
   understands the program).

## The prototype (landed)

`src/remedy/core/builds/reducer.py` (self-contained, no live-loop wiring):

- `Signature` — a symbol's type signature + where it's defined.
- `UnitSpec` / `BuildSpec` — spec-as-schema; the machine writes and enforces the
  API, the model never designs it. `UnitSpec` can carry a `tests` (pytest) body
  so the oracle can falsify *behavior*, not just syntax. `BuildSpec.order()`
  topologically sorts definitions before consumers.
- `SymbolRegistry` — the linker table. `declare`, `lookup`, `references(source)`
  (static AST scan of what a body actually uses), and `closure_text(unit, budget)`
  (minimal context).
- `run_oracle(unit, source)` — the falsification signal: real `compile()` for
  syntax, AST checks that imports and declared symbols exist.
- `PytestOracle` — a behavioral oracle: materializes the current project state,
  writes the unit's `tests`, runs real `pytest`, and turns actual failures into
  an error vector the loop repairs against. This is the strongest signal.
- `build_project(spec, model, ...)` — the reducer: for each pending unit, build
  minimal closure context, call the stateless `model`, verify with the oracle,
  and **re-enqueue falsified units with the error vector in context** until all
  verify. **Always terminates**: a unit is dropped (and reported in
  `BuildResult.failures`) after `max_repairs` extra attempts, and the whole loop
  stops at `max_iterations` — a single bad unit cannot starve siblings.
- `BuildResult` — `ok`, `files`, `iterations`, `repaired`, per-unit `attempts`,
  and a `failures` list with the last error for transparency.
- `materialize(files, root)` / `run_project_tests(files, root)` — write the
  project to disk and grade it with real pytest end-to-end.
- Model hooks: `local_llama_model(...)` (real loopback llama-server via
  `_chat_complete_loopback`, OpenAI-compatible, loopback-only, strips markdown
  fences) and offline mocks `demo_model` (deterministic correct), `demo_weak_model`
  (stochastic weak — emits a syntax error / drops a symbol on the first pass,
  repairs from the oracle error; optional `repair` model for behavioral repair).

`demo.py` bundles a real small project (`order` package: `currency.py`,
`report.py`) with behavioral tests, and `python -m remedy.core.builds` runs it:
`--mode mock|weak|local`, `--budget`, `--max-repairs`, `--defect-rate`,
`--base-url`.

`tests/test_build_reducer.py` (mechanism) + `tests/test_build_reducer_ext.py`
(reliability) prove it with deliberately weak stateless models:
- **minimal context** — each unit's context stays ≤ budget (tested at 120 tokens)
  and excludes unrelated project files;
- **missing-definition repair** — a model that drops a symbol is caught
  ("missing definition: clamp") and converges on the next pass (`repaired >= 1`);
- **syntax-error repair** — a model emitting uncompilable Python is caught by
  `compile()` and converges;
- **cross-file consistency via registry** — the produced file's body references
  its dependency symbol, resolved from the registry, not from seeing the file;
- **livelock protection** — a model that never fixes terminates with `ok=False`
  and a `failures` entry after the repair cap, without starving other units;
- **end-to-end real pytest** — the demo's `order` project builds and its tests
  pass under the deterministic mock (`mode=mock`) and converge under the weak
  model (`mode=weak`) driven by `PytestOracle`, both to green pytest.

13/13 tests pass; ruff + mypy clean.

## Why this is the right answer for "build a project with a small model"

| Naive (fails) | Reducer (works) |
|---|---|
| one long high-reasoning generation | many tiny oracle-verified hops |
| model must hold/recall the project | machine owns state (registry + FS) |
| context grows with project size | context per step is fixed and tiny |
| quality = one model's capability | quality = loop convergence + real verification |
| API designed by (weak) model | API is spec-as-schema, machine-enforced |

The lower parameters / lower reasoning show up only as *more repair iterations*,
not as a hard ceiling — so output quality for structured builds approaches what
a frontier model produces, at the cost of wall-clock (many small local hops).

## How to extend (production roadmap)

1. **Closure via real import/symbol graph** — `references()` already does AST;
   extend to module-level defs/imports (like `pyright`/`mypy` scope resolution)
   for larger projects.
2. **Oracle = real test runner / linter** — `PytestOracle` already does this per
   unit; drive it from the project's real test suite so the error vector is the
   actual `pytest`/`ruff` output (the strongest gradient signal).
3. **Content-addressed output memoization** — reuse the middleman
   (`src/remedy/memory/middleman.py`) so identical units aren't regenerated and
   repairs are keyed by SHA-256.
4. **Drive from spread's read side** — `spread` gathers the map of a repo; feed
   that map into the `BuildSpec` so a red-team/new-project build starts from a
   machine-read target, not a model recall.
5. **Real model hook** — `local_llama_model` is the ready binding to the local
   llama-server; run the CLI with `--mode local` when a server is reachable, and
   set `context_budget` from `resolve_context_window` so a 4k window is honored.
6. **Parallel units** — units without inter-dependencies can be reduced in
   parallel (independent workers), wall-clock drops while quality is unchanged.

## Files

- `src/remedy/core/builds/reducer.py` — the reducer, registry, spec, oracles,
  model hooks, materialize/verify.
- `src/remedy/core/builds/demo.py` — the demo `order` project + runnable driver.
- `src/remedy/core/builds/__main__.py` — CLI (`python -m remedy.core.builds`).
- `src/remedy/core/builds/__init__.py` — public exports.
- `tests/test_build_reducer.py` — mechanism proofs.
- `tests/test_build_reducer_ext.py` — reliability proofs (termination, repair
  cap, end-to-end pytest green).
- `docs/RESEARCH_memory_middleman.md` — the companion state/memory work.

## Related

- `src/remedy/core/spread/` — read/verify fan-out (complement; no write side).
- `src/remedy/memory/middleman.py` — content-addressed working memory.
- `src/remedy/nanoswarm/token_nanobot.py` — `resolve_context_window` (budget source).

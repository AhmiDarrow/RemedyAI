---
name: computational-science
description: >
  Simulation and numerics you can defend: floating-point and conditioning
  understood before blaming the physics, solvers chosen for the problem,
  convergence and refinement studies as evidence, verification separated
  from validation, benchmarks with warm-up and repeats instead of one
  wall-clock reading, and HPC runs that scale on measurement. Use for
  numerical solvers, mesh or timestep studies, profiling and scaling work.
version: 1.0.0
author: Remedy
tags: [simulation, numerics, hpc, performance, reproducibility, research]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_diff, power_analysis, manuscript_build, skill_activate, bash_exec, file_read, file_write]
triggers:
  - '\b(numerical (?:stability|scheme|solver|accuracy)|finite (?:difference|element|volume)|CFL condition)\b'
  - '\b(mesh (?:convergence|refinement)|grid independence|solver tolerance|floating[- ]point (?:error|precision)|catastrophic cancellation)\b'
  - '\b(\bHPC\b|\bMPI\b|OpenMP|SLURM|sbatch|job array|strong scaling|weak scaling)\b'
  - '\b(Monte[- ]Carlo (?:simulation|integration|sampling)|Runge[- ]?Kutta|Crank[- ]?Nicolson|stiff solver|spectral method)\b'
---

# Computational science

`skill_activate(skill="research-method")` first, and do not restate the
spine — that pack owns question framing, evidence standards,
preregistration, citation honesty and "we do not know". This pack owns
the numerics and the machine.

Two failure modes dominate. The first is a number that is wrong for
arithmetic reasons and gets interpreted as physics. The second is a
result that cannot be reproduced because the run was never recorded.
Everything below exists to close one of those.

## Order of work

1. **`analysis_env(path)`** — compilers, MPI, the project's interpreter,
   solver libraries, job scheduler, what is actually installed. Every
   run after this goes through `analysis_run` so argv, input hashes,
   artifacts and duration land in the ledger with a `run_id`.
2. **Check units and dimensions first.** Non-dimensionalise where the
   field does. A dimensional inconsistency explains more failed
   simulations than any solver bug.
3. **Understand the conditioning before choosing a method** —
   `references/floating-point.md`. Ill-conditioned problems lose digits
   no algorithm can recover; a stable algorithm loses no *extra* digits.
   Separate the two before you blame the solver.
4. **Choose the solver deliberately** —
   `references/solver-selection.md`. Stiffness, symmetry, sparsity,
   conditioning and the accuracy you actually need determine it.
5. **Verify the code solves the equations** —
   `references/verification-and-validation.md`: method of manufactured
   solutions, analytic limits, conservation and symmetry invariants,
   observed order of accuracy. This is a separate activity from
   validation against experiment, and it comes first.
6. **Run a refinement study** — `references/convergence-studies.md`.
   Report the observed order, not the theoretical one, and show the
   quantity of interest has stopped moving. A single-resolution result
   is not a result.
7. **Measure performance honestly** — `references/benchmarking.md` and
   `references/profiling.md`. Warm-up, repeats, a spread, and a stated
   definition of what the clock covers.
8. **Scale on evidence** — `references/hpc-jobs-and-scaling.md`. Strong
   and weak scaling curves with efficiency, not one node count and a
   claim.

## Hard rules

- Never compare floating-point values with `==`; never subtract two
  nearly-equal large numbers without checking for cancellation; never
  accumulate a long sum naively when a compensated sum is available.
- Never present a simulation result without stating the discretisation
  (mesh/timestep), the solver tolerances, and the evidence they are fine
  enough. "It looked converged" is not evidence.
- A tolerance is not an error bound. A solver that satisfied its
  tolerance has a small *residual*, which bounds the error only through
  the conditioning.
- Never quote a speedup from a single timed run, and never time the
  first iteration. State what the clock includes (I/O? setup? JIT
  warm-up? MPI init?).
- Every production run records the exact commit, compiler and flags,
  library versions, the input deck, and the RNG seed. `analysis_ledger`
  is where that lives; a figure whose run cannot be re-verified is
  provisional.
- Compiler fast-math and reduced precision change results. If they are
  on, say so and show the difference against a strict-precision run.
- Do not silently switch precision, solver, or preconditioner between
  the runs in one table.

## What "verified" means here

A computational claim is verified when:

- **Code verification** passed: manufactured solution or analytic limit
  recovers the design order of accuracy, and conserved quantities are
  conserved to round-off over the run.
- **Solution verification** passed: a refinement study shows the
  quantity of interest converging, with a discretisation-error estimate
  (Richardson / grid-convergence index) reported next to the value.
- The run re-executes from the recorded argv, inputs and seed through
  `analysis_run`, and `analysis_ledger(action="verify", run_id=...)`
  reports `INTACT`. Where bitwise reproduction is impossible (parallel
  reduction order, non-deterministic scheduling), say so and report
  agreement to a stated tolerance instead of claiming determinism.
- Performance claims come with repeats and a spread, on named hardware.

Validation against measurement is a further, separate claim — with its
own uncertainty on the experiment. Do not merge the two.

## References

Read `references/INDEX.md`, then `file_read` what the task needs.

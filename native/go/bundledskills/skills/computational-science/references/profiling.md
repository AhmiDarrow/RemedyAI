# Profiling

Optimise after measuring, never before. The cost distribution in real
scientific codes is routinely nothing like the author's intuition.

## Order

1. **Establish a correctness baseline** — the verification tests pass and
   a reference output is stored. Every optimisation is checked against it
   (to a stated tolerance; bitwise equality often will not survive a
   reordering, and that is acceptable if the tolerance is justified).
2. **Time the whole thing** and set a target. "Faster" is not a target;
   "the 6-hour parameter sweep in under 1 hour" is.
3. **Profile** at the level that answers the question:
   - **Sampling profilers** (perf, py-spy, Instruments, VTune sampling)
     — low overhead, good first look, safe on production runs.
   - **Instrumenting profilers** (cProfile, Callgrind, Score-P, manual
     timers around phases) — exact call counts, but overhead distorts
     short functions; use for structure, not absolute timings.
   - **Hardware counters** — cache misses, IPC, vectorisation, memory
     bandwidth. This is how you tell compute-bound from memory-bound.
4. **Classify the bottleneck before touching code.** Roofline thinking:
   compute the arithmetic intensity (flops per byte moved) of the hot
   kernel and compare it with the machine's ratio of peak flops to peak
   bandwidth. Below the ridge point the kernel is memory-bound and no
   instruction tuning will help — you need better data layout, blocking,
   or fewer passes over memory.
5. **Fix the top item, re-measure, repeat.** Amdahl bounds you: making a
   routine that takes 20% of the time infinitely fast buys 25%.

## What usually turns up

- **Memory bandwidth**, not flops — most stencil, sparse and particle
  codes. Fixes: loop fusion, blocking/tiling for cache, structure-of-
  arrays layout, avoiding temporaries, in-place updates.
- **I/O** — per-timestep small writes, text output, unbuffered logging,
  metadata storms on a parallel filesystem. Fixes: batch, use a binary
  format (HDF5/NetCDF) with collective writes, write less often.
- **Allocation and copies** — repeated allocation in an inner loop,
  implicit array temporaries, host-device transfers per iteration.
- **The interpreter** in Python/R/MATLAB glue — vectorise, or move the
  kernel to a compiled path in the project's own toolchain (Cython,
  numba, C++, Julia) and call it from the driver script.
- **Load imbalance** in parallel runs (see hpc-jobs-and-scaling.md).
- **Serial setup** — mesh generation, matrix assembly, rank-0 I/O.
  Invisible on small problems, dominant at scale.

## Discipline

Profile at a **realistic problem size**; small cases fit in cache and
lie. Profile on the **target hardware**. Record the profile as an
artifact through `analysis_run` so the before/after pair lives in the
ledger with the commit that produced it.

Report optimisation results as: what the bottleneck was (with evidence),
what changed, the new timing distribution, and the verification result
showing the answer did not change beyond the stated tolerance. An
optimisation without that last part is not finished.

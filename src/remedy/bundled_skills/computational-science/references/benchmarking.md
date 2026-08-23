# Benchmarking

A timing is a measurement, with all the obligations that implies.

## Protocol

1. **Say what the clock covers.** Process start? Input parsing? JIT or
   kernel compilation? MPI init? Allocation? Output writing? Two papers
   timing "the solver" can differ by 3x on this alone. Write the boundary
   into the caption.
2. **Warm up.** Discard the first iterations: caches are cold, JITs have
   not compiled, GPUs have not clocked up, allocators have not settled,
   the filesystem has not cached. Report how many warm-up runs you threw
   away.
3. **Repeat.** A minimum of 5-10 timed repetitions, more when the spread
   is large. One run is not a measurement.
4. **Report a distribution.** Median and interquartile range, or mean
   with SD, plus the number of repeats. The **minimum** is the right
   summary when estimating the machine's capability (noise is one-sided
   and additive); the **median or mean** is right when estimating what a
   user will experience. Say which you chose and why.
5. **Control the machine.** Fixed CPU frequency governor where you can,
   thermal state settled, no other load, pinned threads, same node type.
   Note the hardware: CPU/GPU model, core count, memory, interconnect.
6. **Use a monotonic timer** with adequate resolution, and time enough
   work per measurement that the timer's granularity is irrelevant.

## Fair comparison between implementations

- Same problem, same input, same accuracy. A faster method that converges
  to a looser tolerance has not won — equalise the delivered accuracy, or
  plot **error against cost**, which is the honest picture and shows the
  crossover.
- Same compiler flags and optimisation level, or report both sets.
  `-ffast-math` changes results; if it is on for one side, it is on for
  both, and the numerical difference gets reported.
- Same precision, same parallel layout, same I/O behaviour.
- Verify correctness of both before timing either. A fast wrong answer is
  a common outcome of an optimisation pass.

## Common distortions

- Timing debug builds, or with assertions and bounds-checking on.
- Dead-code elimination removing the thing you meant to measure — consume
  the result (checksum it, write it) so it cannot be optimised away.
- Measuring a cached filesystem read as if it were disk.
- Benchmarking one problem size and reporting it as "the" speedup;
  performance is a curve over size, and small sizes are latency-bound.
- Ignoring first-touch NUMA effects on multi-socket nodes.

## Recording

Drive benchmark runs through `analysis_run` with a `tag` per campaign, so
argv, the input deck hash, environment and duration are in the ledger and
`analysis_ledger(action="diff", run_id="a,b")` can show what changed
between campaigns. Store the raw per-repetition timings as an artifact,
not only the summary.

State the speedup with its uncertainty: "1.9x (median over 10 repeats,
IQR 1.85-1.96) on <hardware>, timing the solve loop only".

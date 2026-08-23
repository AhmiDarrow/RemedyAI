# Variance and seeds

A single training run is one draw from a distribution. Reporting it as
the result is the most common overclaim in empirical ML.

## Where the variance comes from

- **Initialisation** of weights.
- **Data order** — shuffling, batch composition, augmentation randomness.
- **Split randomness** — which rows landed in train vs test. Often the
  *largest* source, and it is invisible if you fix the split.
- **Stochastic training** — dropout, sampling, RL rollouts, decoding
  temperature.
- **Non-determinism in the stack** — non-deterministic GPU kernels,
  atomics, cuDNN algorithm selection, thread counts, mixed precision.
  These make bitwise reproduction hard even with a fixed seed.

Separate them deliberately: varying only the training seed measures
optimisation noise; varying the split seed too measures the quantity you
usually want to report — how much the *conclusion* would move on another
sample.

## How many seeds

Enough that the standard error of the mean is small relative to the
effect you claim. Practically: 5 as a minimum, 10 or more when the effect
is close to the noise, and more for RL or small datasets where spreads
are large. If compute forbids it, say so and widen the claim accordingly
rather than reporting one run as if it were the truth.

Estimate the seed SD once for your setup — it is the yardstick for every
later delta.

## Reporting

- Report **mean ± SD over seeds** (state that it is SD, not SEM, or say
  SEM explicitly), or median with min-max. Give the number of seeds.
- Never report the best seed, never "we report the best of 5 runs", never
  select the checkpoint by test performance.
- Plot the individual runs, not just the aggregate, when there are few.
- A delta smaller than the seed spread is not a result. Say "within
  run-to-run variation" and move on.

## Testing a delta

Pair the runs: same seeds for both methods, so seed-level noise cancels.

- Paired comparison across seeds → `stats_effect_size(kind="cohens_dz",
  values=<per-seed differences>)` for the standardised size, plus the
  mean difference with its CI in the metric's own units.
- With few seeds, a bootstrap or an exact permutation over the paired
  differences is more honest than a t-test on 5 points; report which ran.
- Also bootstrap over **test examples** to get the uncertainty from the
  test set size — that is a different interval from the seed interval,
  and both matter. Report them separately, not merged.
- Comparing many variants → `stats_multiplicity` over the family, and say
  what the family was.

## Determinism

Record and report: framework and CUDA/cuDNN versions, the seed for every
RNG (framework, numpy, python, dataloader workers), whether deterministic
kernels were enabled, and the hardware. State honestly whether runs are
bitwise reproducible or only statistically reproducible — for most GPU
training it is the latter, and that is acceptable if you say it.

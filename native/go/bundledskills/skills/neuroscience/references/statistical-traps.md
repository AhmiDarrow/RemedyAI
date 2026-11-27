# The traps this field is known for

## Circular analysis / double dipping

Using the same data to select and to test. The selective step biases the
statistic, so the reported effect size, t-value and p are not interpretable
— even when the effect is real.

Forms it takes:
- Defining an ROI from the group contrast, then reporting the effect inside
  it as if independent.
- Choosing the electrode, time window or frequency band where the effect
  looks biggest, then testing there.
- Selecting neurons that respond to the condition, then reporting that they
  respond to it.
- Choosing the classifier's features on the full dataset before
  cross-validation.
- Picking the peak voxel and quoting its effect size (peaks are maxima of
  noise plus signal; they are biased upward).

Non-circular alternatives: atlas or anatomically defined ROIs fixed in
advance; ROIs from an independent localiser run; leave-one-subject-out or
leave-one-run-out selection; split-half selection and testing; whole-brain
corrected inference with no selection step at all. Whatever you use, **name
the independence argument in the methods** — that sentence is what a
reviewer looks for.

## Small-n inflation

Low power does not shrink published effects, it inflates them: only large
fluctuations clear threshold, so surviving effects overestimate the truth
(type-M) and can carry the wrong sign (type-S). In practice: pilot-based
sample sizes are systematically too small, a "replication" reaching half the
original effect is not a failure, and a significant result from n = 12 is
weak evidence however small the p. Use `power_analysis` on the second-level
unit and say plainly when the design cannot support the claim.

## Forking paths

Preprocessing options, exclusion thresholds, contrasts, ROIs, covariates,
smoothing kernels, correction methods, time windows: multiply out and the
family of analyses is in the thousands even without intentional fishing.
Defuse it by preregistering the pipeline and the contrast, or by reporting a
multiverse/specification curve, and by keeping exploratory work labelled
exploratory in the paper's own words.

## Reverse inference

"The insula was active, therefore participants felt disgust" runs the
inference backwards. Region-to-mental-state inference requires knowing the
selectivity of that region across tasks, which is what a Neurosynth-style
prior or an explicit forward model supplies. Absent it, report what varied
in the task.

## Others worth naming

- **Non-independent trial selection**: dropping trials on an outcome that
  correlates with the effect.
- **Baseline as an effect**: pre-stimulus differences from filtering or
  attention make post-stimulus effects appear.
- **Brain-behaviour correlations in small samples**: correlations need far
  more subjects than mean differences; 15 points and r = 0.6 is not a
  finding.
- **Voodoo correlations**: correlations computed only in voxels selected for
  a high correlation — the same circularity, applied to r.

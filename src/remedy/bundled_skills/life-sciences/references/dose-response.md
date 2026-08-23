# Dose-response and curve fitting

## Designing the series

- Log-spaced concentrations, not linear: half-log (3.16x) or full-log steps.
- 8-12 concentrations spanning two logs either side of the expected midpoint;
  the curve needs a **defined top and bottom plateau** or the midpoint is
  unidentifiable.
- Include the vehicle (plotted separately — log(0) does not exist) and a
  positive control defining 100% effect.
- Randomise concentration across plate positions; a serial dilution laid out
  left to right aliases concentration onto the plate gradient.
- Replicate biologically (independent plates on independent days), not just
  across wells.

## The model

The standard is the four-parameter logistic (Hill) on log concentration:

    y = bottom + (top - bottom) / (1 + 10^((log10(EC50) - x) * hill))

Fit on log10(concentration) and report all four parameters, not just the
midpoint. Variants: three-parameter (bottom fixed at 0), five-parameter
(asymmetry), biphasic for two-site behaviour — use the simpler model unless
the residuals demand otherwise, and say which you used.

Fitting belongs in the project environment via `analysis_run`: Python
`scipy.optimize.curve_fit` or `lmfit`; R `drc::drm(y ~ conc, fct = LL.4())`,
`nplr` or `nls`. Report the software and version — numbers differ between
packages when weighting or constraints differ.

## Constraints, and being honest about them

Fixing the plateaus to 0 and 100 makes almost any data fit. Do it only when a
control defines that plateau, and say so. If the highest concentration has
not reached a plateau, the IC50 is an extrapolation: report it as "> highest
tested concentration", not as a number with an interval.

Report the **confidence interval** on IC50/EC50, not the point estimate
alone; asymmetric intervals from the log scale are normal and must not be
symmetrised. Report R^2 and inspect residuals — R^2 near 1 is routine for
sigmoid fits and does not indicate a good model.

## Comparing curves

To claim two curves differ, fit them jointly and test the shared-parameter
model against the separate-parameter model (extra-sum-of-squares F, or the
equivalent likelihood-ratio test) rather than eyeballing two overlapping
intervals. State which parameters were allowed to differ.

## Reporting terms precisely

- **IC50/EC50** are relative to the fitted top and bottom of that experiment
  and to the incubation time and cell density. They are not constants.
- **Ki** requires substrate concentration and Km; do not convert an IC50 to a
  Ki without them, and state the Cheng-Prusoff assumptions.
- **LD50** is an animal toxicity endpoint under IACUC review; where a task
  asks for lethal dosing, follow `references/biosafety-and-review.md` — the
  ordinary toxicology stays in scope, weaponisation-relevant specifics do not.
- Normalisation: state what 0% and 100% were, and whether normalisation was
  per plate. Normalising per plate to that plate's own controls is standard
  and must be declared.

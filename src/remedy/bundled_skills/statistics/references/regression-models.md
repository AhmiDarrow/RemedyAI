# Regression models

## Specify before fitting

Write the model down — outcome, predictors, functional form, grouping
structure — and say **why each predictor is in it**. Three reasons, and
they do not mix:

- **Prediction**: anything that helps out of sample; the coefficients are
  not causal and must not be narrated as effects.
- **Causal estimation**: adjust for confounders, never for colliders or
  mediators. Draw the DAG first; "throw everything in" is how you
  condition on a collider and invent an association.
- **Description**: report associations as associations.

Stepwise selection invalidates the SEs, p-values and intervals it then
reports. Do not use it. For prediction with many candidates use penalised
regression (ridge/lasso/elastic net) with the penalty cross-validated,
and report that the coefficients are shrunk.

## Linear

Diagnostics in order: residual vs fitted (linearity), QQ, scale-location
(heteroscedasticity), Cook's D / leverage (influence), VIF
(collinearity). VIF is an inflation of the SE, not a hard cutoff —
collinear predictors do not bias the fit, they make individual
coefficients unstable. If two predictors are near-duplicates, decide
which question you are asking rather than reporting both.

Centre continuous predictors before adding an interaction, or the "main
effects" are effects at x=0, often outside the data.

## Logistic

- Coefficients are log-odds; exponentiate to ORs and say the reference
  level. Report the baseline risk so an OR can be read.
- Separation (a predictor that perfectly splits the outcome) produces
  huge coefficients and infinite SEs — the fit did not converge. Use
  Firth penalised likelihood or drop the predictor and say why.
- Fewer than ~10 events per predictor makes coefficients unstable; the
  events-per-variable rule of thumb is a guide, not a law.
- Assess fit with calibration (predicted vs observed by decile) and
  discrimination (AUC), not a Hosmer-Lemeshow p alone.

## Counts

Poisson assumes variance = mean; check the ratio and switch to negative
binomial above ~1.5. Excess zeros beyond the count model → hurdle or
zero-inflated, saying which story you chose. Use `offset(log(time))`
when exposure differs.

## Mixed / hierarchical

Any repeated measurement, cluster, batch, plate, classroom, subject or
site is a random effect.

- Random intercept per grouping factor; random slopes for effects that
  vary within a group *and* that the design can estimate. If the maximal
  model will not converge, drop correlations then slopes, and report it.
- Fewer than ~5-6 levels of a grouping factor: fit it as a fixed effect —
  the variance component cannot be estimated from 3 clusters.
- Mixed-model p-values are approximate (Satterthwaite/Kenward-Roger) or
  come from LRT / parametric bootstrap. State which.
- Report the variance components and the ICC, not just the fixed effects.

## Verification

Refit with a second library and confirm the coefficients agree to
reported precision, inside `analysis_run` so the data hash and seed are
in the ledger. Held-out predictions beat any in-sample fit statistic.

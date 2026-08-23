# Causal inference without randomisation

Pick one identification strategy on purpose, state its untestable assumption in
the text, and show the diagnostic that would embarrass it.

## DAG first

Draw the graph: treatment, outcome, and every variable you believe causes two of
them. Then read off the adjustment set (back-door criterion) instead of
regressing on everything available.

- **Confounder** (causes treatment and outcome) — adjust.
- **Collider** (caused by treatment and outcome, or by their causes) —
  adjusting *creates* bias. Selection into the sample is often a collider.
- **Mediator** — adjusting removes the indirect path; do not adjust when the
  estimand is the total effect.
- **Post-treatment variable** — never adjust; it is a mediator or a collider.

Name the estimand explicitly: ATE, ATT, LATE, or an effect at a cutoff.

## Difference-in-differences

Assumption: parallel *counterfactual* trends. Evidence, not proof: plot the
pre-period paths, run an event-study specification with leads and lags and show
the pre-treatment coefficients are flat and near zero. A pre-trend test is
underpowered — say so rather than declaring it passed.
With staggered adoption, the classic two-way fixed-effects estimator can be
biased by comparisons of later-treated to already-treated units; use a
heterogeneity-robust estimator and report which. Cluster standard errors at the
level of treatment assignment.

## Instrumental variables

Needs relevance (testable: first-stage F, report it), exclusion (untestable:
the instrument affects the outcome only through treatment — argue it in words),
and monotonicity. IV estimates a LATE for compliers, which is not the ATE, and
weak instruments bias toward OLS while inflating standard errors. Report the
first stage, the reduced form, and the compliers' characteristics.

## Regression discontinuity

Assumption: units cannot precisely manipulate the running variable at the
cutoff. Show the density around the cutoff (a jump is manipulation), show
covariate balance across the cutoff, use local polynomial fits with a
data-driven bandwidth and report sensitivity to bandwidth and polynomial order.
A high-order global polynomial is a known artefact generator. The estimate is
local to the cutoff — say so.

## Matching and propensity scores

Matching adjusts for observed covariates only; it does nothing about unobserved
confounding, and calling it "quasi-experimental" does not change that. Report
standardised mean differences before and after, the common-support region, how
many units were dropped, and the estimand the matching targets (usually ATT).
Prefer balance-checked matching or weighting over a raw propensity-score
regression, and never check balance with a p-value that depends on n.

## Sensitivity

For every design, report how strong an unobserved confounder would have to be to
overturn the result (an E-value or a Rosenbaum bound), and say plainly whether
such a confounder is plausible in this setting.

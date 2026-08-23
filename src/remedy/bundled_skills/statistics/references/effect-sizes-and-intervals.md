# Effect sizes and intervals

The estimate with its interval is the result. The p-value says only
whether the interval excludes the null.

## Which one

| Question | kind= | Notes |
|---|---|---|
| Difference between 2 independent means | `cohens_d`, `hedges_g` | g applies the small-sample correction; use it below n~50 |
| Paired/within difference | `cohens_dz` | dz is not comparable to between-groups d — say which you used |
| Difference vs a control with the control SD | `glass_delta` | when the treatment changes the spread |
| Ordinal / non-normal shift | `cliffs_delta` | pairs with Mann-Whitney |
| Association between two continuous | `pearson_r` | CI via Fisher z |
| 2x2 binary outcome | `odds_ratio`, `risk_ratio`, `risk_difference`, `nnt` | see below |
| Association in a contingency table | `cramers_v` | |
| Variance explained in ANOVA | `eta_squared`, `partial_eta_squared`, `omega_squared` | omega is less biased; partial eta is not comparable across designs |

`stats_effect_size` works from summary statistics (`n1,n2,mean1,mean2,
sd1,sd2` or `a,b,c,d`) or from a data file (`data_path,outcome,group`).
Every payload names its `method` and `accuracy` — quote those when the
interval matters.

## Binary outcomes: pick the scale deliberately

An odds ratio is not a risk ratio, and diverges badly once the baseline
risk exceeds ~10%. For a cohort or a trial, report the **risk difference**
(and NNT) because it carries the baseline; the OR alone lets a tiny
absolute change read as dramatic. Logistic regression gives you ORs — if
you want risks, convert with the baseline stated, or model risk directly.
Never report "a 40% increase" without saying 40% of what.

## Intervals

- Report the confidence level explicitly (95% unless the field says
  otherwise) and the method (`method` field: t-based, Fisher z, Woolf log
  SE, noncentral-t inversion, noncentral F).
- A CI is a statement about the procedure across repetitions, not a 95%
  probability that the parameter is inside this interval. Do not write it
  the second way.
- Interval width is driven by n; a wide interval around a large point
  estimate is an underpowered study, not a large effect.
- Bootstrap intervals (BCa) when the sampling distribution is skewed —
  run them in the project env with a recorded seed and state the number
  of resamples.

## Reporting

Give the estimate in **the outcome's own units first**, then the
standardised version: "22 ms slower (95% CI 8 to 36), d = 0.41 (95% CI
0.15 to 0.67), p = 0.002". A standardised effect alone hides whether the
effect matters; SDs differ across samples, so d is not portable between
studies with different populations.

Refuse to label an effect "small", "medium" or "large" as if it were a
property of the number. Those cutoffs are conventions from one literature.
Say what the effect would mean for the owner's decision instead — that is
what `benchmark_caveat` in the payload is for.

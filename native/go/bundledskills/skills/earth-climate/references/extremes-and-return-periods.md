# Extremes and return periods

## Define the extreme before you fit anything

- **Block maxima** — the annual (or seasonal) maximum, fitted with a generalised
  extreme value distribution. Simple, wasteful of data, needs a long record.
- **Peaks over threshold** — exceedances above a high threshold, fitted with a
  generalised Pareto distribution. Uses more data, but the threshold is a
  modelling choice you must justify and show sensitivity to (mean residual life
  plot, stability of parameters across thresholds).
- **Fixed-threshold counts** — days above 35 degC, days with rain over 20 mm.
  Easy to communicate, but the answer depends entirely on the threshold, and a
  small bias correction near it moves the count a lot.
- Index sets exist for standardised definitions (for example the ETCCDI climate
  indices); use a published definition where one exists and cite it, rather than
  inventing an index that nobody can compare against.

## Independence and declustering

Extreme-value theory assumes independent exceedances. Consecutive hot days or a
multi-day storm are one event. Decluster with a run length or a minimum
separation, state the rule, and report how many events remain — an
undeclustered POT fit understates uncertainty badly.

## Return periods, honestly

A "1-in-100-year event" is a probability statement (about a 1% chance per year)
under a fitted, usually stationary, distribution. Say all of that:

- The estimate is an extrapolation. A 100-year return level from 40 years of
  data has wide uncertainty; report the confidence interval, which is usually
  large and asymmetric, and get it from profile likelihood or a bootstrap rather
  than a normal approximation.
- **Stationarity is likely false** in a changing climate. Either fit a
  non-stationary model with a covariate (global mean temperature, time, an
  index) and report return levels for a stated epoch, or state clearly which
  period the stationary fit represents.
- Return periods are not waiting times and events are not "due". Two 100-year
  events in a decade is unremarkable at some rate; it is evidence only against a
  specified null.
- Spatial pooling (regional frequency analysis) buys precision at the cost of a
  homogeneity assumption. State it.

## Attribution

Framing an extreme as "made N times more likely" is a formal probabilistic
statement from a defined factual/counterfactual pair of ensembles, with the
event definition, region and season fixed in advance. It is a substantial method
in its own right — do not produce such a number from a single model run or a
post-hoc event definition, and do not attach it to a figure informally.

## Reporting

Event definition, data source and version, block or threshold and its
justification, declustering rule, distribution and fitting method, parameter
estimates with uncertainty, a return-level plot with intervals, and a
goodness-of-fit diagnostic. Run the fit through `analysis_run`.

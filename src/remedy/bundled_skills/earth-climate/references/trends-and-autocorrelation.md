# Anomalies, baselines and trends

## Anomalies

An anomaly is a difference from a climatology, and the climatology is a choice:

- **Baseline period** — say which years. Changing the baseline shifts every
  number in the series and makes two figures incomparable. Standard reference
  periods exist (WMO publishes them); state the one used rather than assuming
  the reader knows.
- **Construction** — per calendar month, per day-of-year, harmonic fit, or a
  smoothed daily climatology. Per-day climatologies from short baselines are
  noisy; smooth them and say how.
- **Additive vs multiplicative** — temperature anomalies are usually additive,
  precipitation is often better as a ratio or on a transformed scale.
- Anomalies computed per grid cell before averaging are not the same as the
  anomaly of the area mean when data coverage changes over time. With missing
  data, this difference *is* the coverage bias.

## Trends

- **Serial correlation is the default in geophysical series.** Ordinary
  least-squares standard errors assume independent residuals and are far too
  small. Report the trend with an interval computed from an effective sample
  size (a lag-1 AR(1) adjustment at minimum) or from a method that models the
  correlation, and name which was used.
- **Effective sample size** under AR(1) with lag-1 correlation r is roughly
  n(1-r)/(1+r); this is an approximation and should be labelled as one. Long-
  memory processes need more care.
- **Mann-Kendall** is the common non-parametric trend test; it also assumes
  independence, so use prewhitening or a variance correction, and pair it with a
  Theil-Sen slope for the magnitude.
- **Seasonality** must be removed or modelled before a trend test, and removing
  it with a climatology from the same record slightly reduces the residual
  variance — acceptable, but say so.
- **Endpoint sensitivity**: recompute the trend over several start and end years
  and show the spread. A trend that only exists for one window is a window, not
  a signal.
- **Breakpoints and inhomogeneity**: station moves, instrument changes, satellite
  transitions and reanalysis assimilation changes create steps. Test for change
  points and check the product's own homogenisation documentation before
  interpreting a step as physics.

## Autocorrelation elsewhere

Spatial autocorrelation inflates significance in field comparisons too: grid
cells are not independent samples, so a map of pointwise p-values will show
"significant" patches by chance. Use a field-significance or false-discovery-rate
approach across the map and say which.

## Reporting

Trend value, units per decade, interval, the method for the interval, the period,
the dataset version, and the detrending/deseasonalising steps in order. Run it
through `analysis_run`; `stats_assumptions` on the residuals will tell you
whether the normality-based interval was ever appropriate.

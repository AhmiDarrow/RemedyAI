# Fitting and goodness of fit

## Pick the estimator on purpose

- **Least squares** is maximum likelihood only when residuals are Gaussian
  with known variances. Weight by 1/sigma_i^2; an unweighted fit assumes
  equal errors, which is a claim, not a default.
- **Poisson likelihood** for counts. A chi-square on bins with few expected
  counts biases the result; rebin, or fit the likelihood.
- **Errors on both axes** need orthogonal-distance regression or an explicit
  likelihood; ordinary least squares on x-uncertain data biases the slope
  toward zero.
- **Correlated residuals** need the full covariance in the chi-square.

## Reading chi-square

Report chi2, the degrees of freedom (points minus fitted parameters minus
constraints), the ratio, and the p-value.

- chi2/dof near 1: residuals are consistent with the uncertainties you
  assigned. That is a statement about your error bars, not about the model.
- chi2/dof well above 1: the model is wrong, an uncertainty is understated,
  or a systematic is unmodelled. Look at residuals before inflating errors;
  scaling errors to force chi2/dof = 1 hides the problem and must be
  declared if done.
- chi2/dof well below 1: uncertainties are overstated, or correlated errors
  were treated as independent.

## Covariance and correlation

Always report the covariance matrix (or the correlation matrix plus the
standard errors). Two anti-correlated parameters can each look poorly
determined while their sum is tight — a reader given only the diagonal cannot
propagate your result. Check a Hessian-based covariance against a
profile-likelihood or bootstrap interval near any physical boundary.

## Good fit versus good model

Diagnostics that catch a wrong model that still fits well:

- Residuals against every independent variable and against the fitted value
  — look for curvature, steps, heteroscedasticity, runs.
- A runs test or residual autocorrelation for ordered data.
- Fit a nested alternative and compare with a likelihood-ratio test; for
  non-nested models compare AIC/BIC, and say which and why.
- Hold out part of the range and predict it.
- Refit on subsets (run, detector, epoch); disagreement beyond the errors is
  a systematic.

## Boundaries and pathologies

- A parameter pinned at a bound has no meaningful symmetric error; profile
  the likelihood.
- The asymptotic chi-square form of 2*Delta log L needs regularity
  conditions that fail at boundaries and for parameters absent under the
  null; calibrate the test statistic by toy Monte Carlo and say you did.
- Report the fit range and every cut. A range chosen after seeing the result
  is post-hoc and belongs in the systematics.

## In this repo

`stats_assumptions` reports normality, variance homogeneity and outliers with
verdicts — read its `what_this_cannot_tell_you` list rather than treating a
pass as licence. Fitting runs in the project's own environment through
`analysis_run`, so script, inputs and outputs are hashed into the ledger.

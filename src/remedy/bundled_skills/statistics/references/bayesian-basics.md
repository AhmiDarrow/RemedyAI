# Bayesian analysis

## When it is the honest choice

- **Evidence for the null.** A frequentist non-significant result cannot
  support "no effect". A Bayes factor or a posterior concentrated in a
  region of practical equivalence (ROPE) can.
- **Small n with real prior information** — historical controls, a
  physical constraint, a previous replication. The prior is doing declared
  work instead of hidden work.
- **Hierarchical/partial pooling** across many groups, sites or features.
- **Sequential monitoring.** The posterior does not depend on when you
  looked, though the design still needs a stated stopping rule.
- **Direct probability statements.** When the owner needs P(effect > 0 |
  data) rather than a tail probability under a null nobody believes.

It is not a rescue for a weak design, and it does not remove the need for
a pre-registered analysis plan.

## Doing it properly

1. **Specify the model and the priors before the data**, justifying each
   prior in the parameter's units ("effects above 20 ms are implausible
   here because ..."). Weakly-informative defaults are fine when stated.
2. **Prior predictive check** — simulate datasets from the prior. If they
   contain impossible values, the prior is wrong, and you found out before
   seeing data.
3. **Fit** through `analysis_run` in the project env (Stan, PyMC, brms,
   NumPyro — none of them exist in the sidecar, so they run as a child
   process with the seed and versions recorded).
4. **Convergence, every time**: R-hat < 1.01 on all parameters, bulk and
   tail ESS in the hundreds at least, zero divergent transitions, and
   trace plots that mix. Divergences signal broken geometry —
   reparameterise (non-centred) rather than raising adapt_delta until
   they hide.
5. **Posterior predictive check** — does the fitted model generate data
   that look like the observed data? Report it, not just the fit.
6. **Prior sensitivity** is mandatory: refit with at least one wider and
   one narrower prior and report whether the conclusion moved. A result
   that survives only one prior is a result about the prior.

## Reporting

Report the posterior median (or mean) with a credible interval, and say
it is a credible interval — that phrasing *is* allowed to mean "95%
posterior probability", unlike a confidence interval. Add the ROPE and
the posterior probability inside it when the question is equivalence.

Bayes factors depend strongly on the prior for the alternative; report
the prior and a sensitivity range, and do not translate a BF into a
significance verdict with a fresh set of thresholds.

## Verification

The fit is verified when it reruns from the same seed and data hash under
`analysis_run` with the same posterior summaries, convergence diagnostics
pass, and a parameter-recovery run on data simulated from known
parameters returns those parameters inside their intervals at the
nominal rate.

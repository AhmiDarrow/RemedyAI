# Sampling and what it licenses

## Write these down before the n

- **Target population** — who the claim is about.
- **Sampling frame** — the list you could actually draw from. The gap between
  frame and target is coverage error, and it is a property of the study, not of
  the respondents.
- **Selection mechanism** — probability (simple random, stratified, cluster,
  multistage) or non-probability (convenience, quota, snowball, river/panel
  opt-in). Say which.
- **Response/participation rate** and how it was computed. Rates are only
  comparable when the disposition definitions match; the AAPOR standard
  definitions are the usual reference — check the current edition rather than
  quoting a formula from memory.
- **Weights** — design weights, non-response adjustment, post-stratification or
  raking, and the variables raked to. Report unweighted and weighted estimates
  when they differ; a big divergence is information, not a nuisance.

## Non-probability samples

Convenience samples (students, one platform, one clinic, crowd workers) support
"in this sample". Going wider requires an argument that selection is ignorable
for the relationship being studied — plausible for some psychological
mechanisms, implausible for prevalence, turnout, income or anything the
selection variable correlates with. Prevalence from a convenience sample is
almost never defensible; a within-person experimental contrast often is.

Sample matching and weighting improve non-probability samples only for the
variables you matched on. Say what was matched and what was not.

## Non-response and attrition

- Compare responders and non-responders on any frame variable available.
- For panels, report attrition per wave and test whether it predicts baseline
  outcome; differential attrition between arms breaks randomisation.
- Distinguish unit non-response from item non-response; do not silently
  listwise-delete. Report the analytic n for every model and where it came from.
- Missing-data mechanism (MCAR / MAR / MNAR) is an assumption, not a test
  result. Little's test does not prove MCAR. Multiple imputation is MAR-based;
  say so, report the number of imputations and the variables in the model.

## Power and precision

Use `power_analysis` with the smallest effect worth detecting, not a published
point estimate — published effects are inflated by selective publication.
For clustered designs pass `clusters` and `icc`: the design effect
1 + (m-1)*ICC can multiply the required n several-fold. Inflate for expected
`dropout`. For a descriptive estimate, plan on the width of the interval rather
than on significance.

## Reporting

State population, frame, mode (web/phone/face-to-face/administrative), field
dates, incentives, n invited / n started / n analysed, weights, and the
generalisation sentence you are willing to defend. Field dates matter: a survey
run during an unusual week is a measurement of that week.

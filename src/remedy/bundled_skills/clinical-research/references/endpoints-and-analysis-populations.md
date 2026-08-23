# Endpoints, estimands, analysis populations

## One primary endpoint

Fixed before data, in the protocol and the registry, with: the measure, the
timepoint, the metric (change from baseline, responder, time-to-event), the
comparison, and the population it is computed in. Everything else is
secondary, is labelled secondary in every table, and carries a multiplicity
plan (`stats_multiplicity`).

**Moving the primary endpoint after data exist destroys the trial's error
control.** The nominal p-value no longer means what it says, because the
choice was informed by the outcome. If it happened, it is reported as a
protocol amendment with date and reason, and both the original and the new
endpoint are shown. Never present the post hoc winner as the plan.

- **Composite endpoints** are driven by their commonest, usually least
  severe component. Report each component separately alongside the
  composite; a composite that moves only on "hospitalisation" is not a
  mortality result.
- **Surrogate endpoints** (LDL, HbA1c, tumour response, viral load) license
  a claim about the surrogate. Claiming the clinical benefit requires
  evidence that the surrogate mediates it in this population and
  intervention class — say so rather than eliding it.
- **Patient-reported outcomes** need a validated instrument, its recall
  window, its minimal important difference, and a plan for missing items.

## Estimand first, method second

Before choosing a model, state what would happen to the estimate under each
intercurrent event: treatment discontinuation, rescue medication, death,
crossover. The strategy (treatment policy, hypothetical, composite,
while-on-treatment, principal stratum) determines the analysis. Picking the
model first and describing the estimand afterwards is how two papers with
the same data disagree.

## Analysis populations

- **ITT** — everyone as randomised, regardless of what they received.
  Primary for superiority. It preserves randomisation and answers the
  policy question "what happens if we offer this".
- **mITT** — a pre-specified, narrowly justified subset (e.g. received at
  least one dose). Define it in the protocol or it becomes a place to hide
  exclusions.
- **Per-protocol** — completers who adhered. Always secondary in a
  superiority trial; adherence is post-randomisation and confounded.
- **As-treated** — by what was actually received; useful for safety.
- **Safety population** — everyone exposed. Harms are reported here.

## Missing data

Not a nuisance parameter — a threat to validity. Pre-specify: the assumed
mechanism (MCAR/MAR/MNAR), the primary handling (usually multiple imputation
or a mixed model for repeated measures under MAR), and at least one
sensitivity analysis under a departure from it (tipping point, delta
adjustment). Last-observation-carried-forward is not acceptable as the
primary method. Report the amount and pattern of missingness per arm; a
difference between arms is itself a finding.

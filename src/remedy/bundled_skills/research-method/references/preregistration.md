# Preregistration

## Why it is the cheap half of credibility

Preregistration separates prediction from explanation. It does not restrict
what you may do — it fixes what you may *call confirmatory*. Everything else
stays available, labelled exploratory.

## When the field expects it

- **Clinical trials** — registration before enrolment is required by most
  journals and regulators (ICMJE policy; ClinicalTrials.gov, ISRCTN, and the
  WHO ICTRP network of primary registries). Check the current requirement at
  the registry and the target journal — do not assert a rule from memory.
- **Systematic reviews** — protocol registration is the norm (PROSPERO for
  health; other registries elsewhere).
- **Psychology, ecology, economics, political science, and increasingly ML
  and metascience** — expected for confirmatory claims; often on OSF or a
  journal's Registered Report track.
- **Registered Reports** — the plan itself is peer reviewed and accepted in
  principle before data collection. The strongest form; slow, and the owner
  decides whether the timeline fits.

When the field does not expect it, still write the plan. A dated, read-only
`preregistration.md` committed before the data arrives costs an hour and
answers most "did you decide that afterwards" questions.

## What goes in

1. Question, H0/H1, direction, and the smallest effect of interest.
2. Design: units, assignment, arms, blinding, randomisation mechanism.
3. Sample size with its justification, plus the stopping rule (fixed n, or
   the sequential design and its boundaries).
4. Variables: primary outcome (exactly one), secondary outcomes, covariates,
   manipulation checks — each with its instrument and scoring.
5. The analysis, fully specified: model, test, one- or two-sided,
   alpha, covariates, transformations, how missing data is handled, outlier
   and exclusion rules with numeric criteria, multiplicity correction and the
   family it applies to.
6. What would count as support, what would refute, and what would be
   inconclusive.
7. Anything already known about the data ("no data collected yet", or exactly
   what has been seen — pilot, prior wave, public dataset you have inspected).
8. Planned exploratory work, named as exploratory.

## Timestamping and immutability

Register before collection where a registry exists. Otherwise: commit the
plan file, tag it, and record the commit hash — the hash is the timestamp.
Never edit it in place afterwards.

## Deviations

Deviations are normal; hiding them is not. Keep a deviations table in the
manuscript: what was planned, what was done, when the change was decided,
and why. A change decided before unblinding is much weaker evidence of bias
than one decided after — say which.

## Preregistration does not

...make a result true, fix a bad design, or license skipping assumption
checks. And an unregistered analysis is not automatically wrong — it is
exploratory, and reported as generating a hypothesis rather than testing one.

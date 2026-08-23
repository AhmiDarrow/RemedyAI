# Trial and observational designs

## Randomised, and what each shape buys

- **Parallel-group.** Default. Randomise participants to arms, compare
  between arms. Simple to analyse, robust to period effects, needs the most
  participants.
- **Crossover.** Each participant gets both treatments in random order; the
  participant is their own control, so n falls sharply. Only for stable
  chronic conditions with a reversible, short-acting effect. Requires a
  washout long enough that carryover is implausible, and the analysis must
  test period and sequence effects. Dropouts hurt more than in parallel.
- **Cluster-randomised.** Randomise clinics, wards, schools. Use when the
  intervention is delivered to a group or contamination between individuals
  is unavoidable. Outcomes within a cluster correlate: inflate n by the
  design effect 1 + (m-1)*ICC, and analyse with a mixed model or GEE that
  keeps the clustering. Recruiting participants after the cluster is
  allocated invites identification bias.
- **Stepped-wedge.** Clusters cross one way, control to intervention, in a
  randomised order until all are exposed. Fits roll-outs that cannot be
  withheld. Confounded with secular time, so time must be in the model; it
  is not a cheap cluster trial.
- **Factorial.** Two interventions in one trial (2x2). Efficient only if
  the interaction is plausibly nil; power for the interaction itself is far
  lower than for the main effects.
- **Non-inferiority / equivalence.** Margin is chosen before data, clinically
  justified, and defended in the protocol. Here per-protocol and ITT are
  both reported, because sloppiness biases toward non-inferiority.
- **Adaptive / platform.** Pre-specified rules for dropping arms, reweighting
  allocation or re-estimating n, with the type-I error controlled by design.
  Adaptation invented mid-trial is not adaptive design, it is data-driven
  change.

## Observational, and what it cannot do

- **Cohort** (prospective or retrospective): exposure precedes outcome, can
  estimate incidence and relative risk, vulnerable to confounding and loss
  to follow-up.
- **Case-control**: efficient for rare outcomes; gives odds ratios, not
  risks. Control selection and recall bias are the whole ballgame.
- **Nested case-control / case-cohort**: cases and sampled controls drawn
  from an established cohort — keeps the sampling frame defensible and the
  assay cost low.
- **Cross-sectional**: prevalence and association only; no temporality.
- **Self-controlled case series / case-crossover**: each person is their own
  control, so time-invariant confounding drops out; needs transient exposure
  and an outcome that does not alter later exposure.

No observational design licenses "causes" on its own. State the
identification strategy and the confounders measured, list the ones not
measured, and describe the sensitivity analysis (E-value, negative control
outcome, quantitative bias analysis) that would overturn the result.

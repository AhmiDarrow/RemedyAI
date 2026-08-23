# Study design

## Decide these, in order

1. **Unit of analysis.** Person, cell, well, plot, session, run. Then
   the **unit of assignment** — assign clusters and analyse individuals,
   and the analysis must model the clustering or the intervals lie.
2. **Comparison.** Between-subject, within-subject, matched pairs, or none
   (descriptive). One arm answers "what happened", never "did it work".
3. **Assignment.** Randomised (mechanism, allocation ratio, concealment,
   stratification/blocking) or observational (the identification strategy
   — matching, adjustment, instrument, difference-in-differences,
   regression discontinuity — and its assumption).
4. **Controls.** Negative (shows nothing), positive (shows the known effect),
   vehicle/sham, and a baseline that is honest current practice rather than
   a weakened one.
5. **Blinding.** Who is blind: participants, deliverer, assessor, analyst.
   It matters most for subjective outcomes. Where blinding is impossible,
   say so, and say what you did instead.
6. **Replicates.** Independent replicates carry the inference; technical
   ones measure the instrument. Never pool the two.
7. **Sample size.** From the smallest effect of interest, not from what is
   convenient — the statistics pack sizes it. Record the assumed effect,
   alpha, power, dropout, and the design effect if clustered.
8. **Randomise everything else.** Run order, plate, cage, batch, session
   time, machine. Batch confounded with condition is unrecoverable.

## Confounders

List them, then say how each is handled: by design (randomisation,
restriction, matching, blocking) or at analysis (adjustment,
stratification). A causal diagram (DAG) makes the adjustment set arguable
instead of intuitive, and shows what must *not* be adjusted for.

## Validity threats to check off

- **Selection** — who got in, who dropped out, and whether that relates to
  the outcome.
- **Measurement** — instrument validity and reliability, ceiling/floor,
  drift, misclassification that differs between arms.
- **Attrition** — expected loss; intention-to-treat vs per-protocol,
  decided now. **Contamination** — controls receiving the exposure.
- **Regression to the mean** — an extreme baseline guarantees apparent
  improvement.
- **Multiplicity** — count the outcomes, timepoints, subgroups and models the
  design implies. That count is the family; handle it in the plan.
- **External validity** — the population, setting and doses you may speak
  about afterwards.

## Common traps

- Optional stopping. Fix n, or use a prespecified sequential design.
- Subgroup fishing — subgroups are prespecified and interaction-tested.
- Pseudoreplication — many measurements from one subject counted as n.
- Skipping the pilot. It sizes variance and finds failure modes.
- Feasibility unchecked: recruitment rate, instrument time, storage, cost. A
  design that commits money, shared instrument time, or another person's data
  goes to the owner before it goes in the plan.

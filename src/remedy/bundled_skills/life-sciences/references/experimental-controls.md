# Experimental controls

Every control answers one specific objection. Name the objection first, then
pick the control. A control run on a different day or plate answers nothing.

## The standard set

| Control | Objection it answers |
|---|---|
| Untreated / baseline | Did anything change at all? |
| Vehicle (DMSO, saline, buffer at the same final %) | Was it the compound or the solvent? |
| Positive | Would the assay have detected a real effect today? |
| Negative (no target, scrambled siRNA, non-targeting gRNA, knockout line) | Is the signal specific to the target? |
| Isotype / secondary-only (IF, flow, IHC) | Is the stain the antibody or background? |
| No-template, no-RT (PCR/qPCR) | Contamination; genomic DNA in the RNA prep |
| Unstained + single-stain (flow) | Autofluorescence; compensation/spillover |
| Sham surgery / sham injection | Was it the intervention or the handling? |
| Internal/loading (blot, qPCR reference gene) | Did equal material get loaded? |
| Spike-in / standard curve | Is the readout in the linear range? |

## Rules

- Controls belong in the **same plate, same run, same day, same operator** as
  the samples they defend. A historical control is a different experiment.
- A positive control that fails invalidates the run — the negatives are
  uninterpretable, not "clean". Say the run failed; do not report the
  treatment arms from it.
- The vehicle concentration must match the highest treatment concentration,
  not the average. DMSO above roughly 0.1-0.5% is itself an intervention in
  many cell types; check the value for the line in use.
- Knockout/knockdown rescue is the strongest specificity control available in
  cell biology: does re-expressing the target restore the phenotype? An
  off-target effect will not rescue.
- Two independent reagents against the same target (two siRNAs, two gRNAs,
  two antibodies from different clones) beat one reagent used twice.
- Blinded scoring is a control. If a human reads the outcome and knows the
  arm, the control set is incomplete.

## When a control is missing

Say what cannot be concluded, specifically. "No vehicle arm, so the effect
cannot be separated from the solvent" is useful. "Some limitations exist" is
not. Do not model or subtract the missing control from another run; propose
the smallest repeat experiment that recovers it, with the arms and n.

## Assay-level sanity checks before analysis

- Linear/dynamic range: is any sample at the ceiling or floor of the
  detector? Saturated wells are censored data, not high values.
- Standard curve R^2 and back-calculated recovery of the QC points.
- Plate edge effects: compare edge wells to interior for the same condition.
- Carry-over: does a blank following a high sample read high?

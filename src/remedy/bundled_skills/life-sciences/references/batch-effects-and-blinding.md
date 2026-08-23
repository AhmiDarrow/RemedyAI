# Batch effects, randomisation and blinding

## The failure mode

Treated samples processed on Tuesday, controls on Thursday. The difference is
real and unattributable forever — no statistic separates treatment from day.
The same holds for plate, passage number, reagent lot, column of a multichannel
pipette, instrument, operator and freeze-thaw cycle.

**Rule: every batch contains every group.** If only four samples fit per run,
run one of each group per batch, not all of group A first.

## Designing the batch out

1. List the nuisance factors that will vary: day, plate, operator, lot,
   instrument, cage, litter, passage.
2. Block on the ones you can (equal groups per block), randomise within
   blocks, and record every one of them as a column in the data file.
3. Where a factor cannot be balanced, record it anyway so it can enter the
   model as a covariate. An unrecorded batch cannot be adjusted for.
4. Keep passage number inside a stated window and record it per replicate;
   phenotypes drift with passage.

## Randomisation you can actually reproduce

Generate the allocation with a seeded program and save it as a file next to
the data, rather than shuffling by hand:

```python
import random
random.seed(20260101)          # record the seed in the notebook
units = [f"M{i:02d}" for i in range(1, 25)]
random.shuffle(units)
arms = {u: ("treat", "vehicle")[i % 2] for i, u in enumerate(units)}
```

Randomise **allocation**, **position on the plate/rack** and **processing
order**. Position randomisation is what defeats edge effects and thermal
gradients; leave the outer wells empty or filled with buffer where the assay
is known to suffer at the edge.

## Blinding — who, to what, when

| Stage | Blind whom | How |
|---|---|---|
| Allocation | the person assigning | sealed list from a third party |
| Intervention | the operator, where possible | coded vials A/B/C |
| Outcome measurement | always, where a human reads it | rename files to codes before scoring |
| Analysis | the analyst | arm labels swapped for X/Y until the model is fixed |

The cheapest and most valuable is outcome blinding. Rename image or trace
files to random codes with a key file held outside the analysis directory,
score, then join on the key. Histology scores, behavioural scores, manual
counts and threshold choices all move measurably when the scorer knows the
arm.

## Detecting a batch effect after the fact

`data_profile` the file, then plot the outcome by batch within each arm. If
batch explains more variance than treatment, say so before reporting the
treatment effect. Adjusting for batch is legitimate only when the design was
not fully confounded; if it was, no adjustment recovers it — the honest
output is "this needs a repeat with groups split across runs".

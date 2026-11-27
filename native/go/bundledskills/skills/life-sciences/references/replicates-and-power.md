# Replicates, pseudo-replication and power

## Deciding n

n is the number of units that were **independently randomised or
independently sourced**. Work backwards from the randomisation: whatever the
allocation was applied to is the unit.

- Compound added per well, wells from one flask -> the flask is the unit.
- Compound added per flask, flasks thawed separately -> the flask is the unit
  and n = number of flasks.
- Treatment assigned per cage, outcome measured per mouse -> the cage is the
  unit unless the model accounts for cage.
- Slices from one brain, cells from one dish, fields from one slide, reads
  from one library: never independent.

## Nesting instead of discarding

Averaging technical replicates to one value per biological unit is always
defensible and usually enough. When the within-unit variation matters (single
cell measurements, repeated sampling over time), keep every observation and
fit a mixed model with the biological unit as a random intercept:

- R: `lmer(y ~ treatment + (1 | animal), data = d)`
- Python: `statsmodels.formula.api.mixedlm("y ~ treatment", d, groups=d["animal"])`

Both belong in the project environment — run them through `analysis_run`, not
in the sidecar. Report the number of units and the number of observations
separately: "n = 5 mice, 312 cells".

## Sizing a study

`power_analysis(test="two_sample_t", solve="n", effect_size=..., alpha=0.05,
power=0.8)` needs an effect you are willing to defend. Sources, in order of
strength: a preregistered smallest effect of interest; a published effect in
the same system with its interval; a pilot. A pilot SD from n = 3 is very
imprecise — treat the resulting n as a lower bound and say so.

Never solve for effect size from the data you already collected and present
it as the study's power. Post-hoc power computed from the observed effect is
a monotone function of the p-value and carries no information.

For unequal groups use `ratio`; for expected attrition use `dropout`; for
cage/litter/plate clustering use `clusters` and `icc` so the design effect is
applied. `sensitivity=True` gives the minimum detectable effect at the n the
owner can actually afford — that is usually the more useful number.

## The three-replicate habit

"n = 3" is a convention, not a design. Three biological replicates give a
very poor SD estimate and cannot support a claim about a modest effect. When
the owner asks for three, say what it can and cannot detect (run
`power_analysis` with `solve="effect_size"` at n = 3) and let them choose.

## Reporting

State in the legend: the unit, the number of units, the number of technical
replicates averaged, the number of independent experiments, and whether error
bars are SD, SEM or a CI. SEM bars on n = 3 look tight and mean almost
nothing; prefer showing the individual unit values as points.

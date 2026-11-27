# Interpreting results

## Read the estimate, not the asterisk

Report, in this order: the effect size in the outcome's own units, its
confidence (or credible) interval, the n it came from, and only then the test
statistic and p. A p-value is the probability of data this extreme under H0 -
it is not the probability that H0 is true, not the size of the effect, and not
the probability the result replicates.

The interval does most of the interpretive work. Ask what the two ends mean
in practice: if both ends are trivial, the study rules the effect out. If the
interval spans trivial and important, the study is uninformative - say that.

## What a null result means

"Not significant" is not "no effect". Distinguish:

- **Evidence of absence** - a tight interval around zero that excludes
  everything you would care about.
- **Absence of evidence** - a wide interval; the study could not decide.
  Report the minimum detectable effect at this n.
- Equivalence and non-inferiority need their own prespecified margin and test;
  a failed superiority test does not establish equivalence.

## Effect size discipline

Small/medium/large labels are field conventions, not facts. Say what the
effect means where it lives: minutes saved, percentage points, deaths averted,
points on a validated scale, standard deviations only when nothing better
exists. Compare it to the smallest effect of interest fixed at design time.

Watch the inflation: statistically significant estimates from underpowered
studies are systematically too large (winner's curse). A surprisingly big
effect from a small n is a reason for suspicion, not excitement.

## Causal language

Randomised assignment with intact blinding earns "caused". A credible
identification strategy earns "consistent with a causal effect of, under
<assumption>". Everything else gets "associated with", and the assumption is
named. Never let the abstract be more causal than the design.

## Before believing your own result

- Does it survive the prespecified sensitivity analyses?
- Is it driven by a handful of rows? Re-run without them and say what changed.
- Could it be the batch, the instrument, the run order, the site, the split?
- Is the direction physically or biologically plausible, and if not, what
  would explain it?
- Would the pipeline produce it on shuffled labels? Try it.
- `data_diff` the dataset against the version the plan was written for.

## Generalisation

State the population, setting, doses, versions and timeframe the result
speaks about - and the ones it does not. A benchmark result generalises to
that benchmark until someone shows otherwise.

## The discussion

Lead with what the data support, then the limitations that could overturn it,
then what would settle it next. Limitations are specific and consequential
("recruitment from one clinic; the effect may not hold where baseline
severity is lower"), never a ritual paragraph.

# Question and hypothesis

## Sharpen the question

Rewrite the owner's ask until every slot is filled. The usual frames:

- **PICO** (intervention): Population, Intervention, Comparator, Outcome,
  Timeframe. **PECO** (observational): Exposure instead of Intervention.
- **Descriptive**: population, quantity, instrument, precision.
- **Mechanistic**: system, perturbation, readout, controls.
- **Methods/benchmark**: task, baseline, metric, dataset and split.

Then run these checks:

1. **Falsifiable?** Name at least one concrete result that would make the
   claim wrong. If nothing could, the question is a slogan — fix it now.
2. **Measurable?** Every noun maps to something you can record. "Engagement",
   "quality", "health" are placeholders until an instrument is named.
3. **Bounded?** One question. Compound questions ("does X work and why and
   for whom") become a primary and named secondaries.
4. **Answerable here?** With the data, time, instruments and approvals
   actually available. If not, say which is missing before designing.
5. **Worth asking?** Already settled → the answer is a citation, not a study.

## Question types drive design

| Question word | Design that answers it |
|---|---|
| Does X cause Y? | randomised assignment, or a credible identification strategy |
| Is X associated with Y? | observational + confounder control; no causal verbs |
| How much / how many? | estimation with a precision target, not a hypothesis test |
| Does A beat B? | matched comparison, prespecified metric, honest baseline |
| How does X work? | mechanism: perturb one thing, control the rest |

## Hypothesis

Write all four before touching data:

- **H0** — the specific null (usually "no difference / no association"), with
  the parameter named: "no difference in mean 30-day score between arms".
- **H1** — direction and, where possible, a magnitude that would matter.
  "Smallest effect size of interest" beats "some effect".
- **Refutation condition** — the result that would make you abandon H1.
  Write it now; after seeing data it becomes unwritable.
- **Alternative explanations** — confounders, selection, measurement error,
  reverse causation, and what in the design handles each.

## Traps

- **HARKing** — presenting a hypothesis formed after the results as if it was
  prior. If the idea came from the data, it is exploratory. Label it.
- **Unfalsifiable rescue** — every disconfirming result gets a new excuse.
  Decide in advance which rescues are allowed.
- **Outcome switching** — the primary outcome is fixed at design time. A
  different one that "worked" is a secondary, reported as such.
- **Vague population** — "users", "patients", "cells" without inclusion and
  exclusion criteria generalises to nobody.

## Output of this stage

A short block in the plan file: question, population, exposure, comparator,
outcome + instrument, timeframe, H0, H1, refutation condition, smallest
effect of interest, and the alternatives you will have to rule out.

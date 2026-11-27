# Measurement validity and reliability

A measure can be perfectly reliable and measure the wrong thing. Report both,
and never treat a reliability coefficient as evidence of validity.

## Validity, in the order it usually matters

- **Content** — do the items cover the construct's domain? Judged by experts and
  by the definition you wrote down, not by a statistic.
- **Construct** — does the measure behave as the theory says? Convergent
  (correlates with measures of the same thing), discriminant (does not correlate
  with things it should not), known-groups (separates groups that should differ).
- **Criterion** — concurrent or predictive correlation with an external outcome.
  Report the criterion and its own error.
- **Structural** — does the factor structure hold? Exploratory factor analysis to
  find it, confirmatory factor analysis on *different* data to test it. Fitting
  and testing on the same sample is circular.
- **Measurement invariance** — before comparing groups (countries, waves,
  languages), test configural, metric and scalar invariance. Comparing latent
  means without at least partial scalar invariance compares different rulers.

## Reliability

- **Internal consistency**: Cronbach's alpha assumes tau-equivalence and rises
  with item count; McDonald's omega is usually the better default for a
  multi-item scale. Report which was computed, on which items, with n.
- Conventional thresholds (0.70 and friends) are conventions, not laws. If a
  threshold is cited, cite the source actually read.
- **Test-retest**: correlation or ICC across an interval that is long enough that
  memory does not carry, short enough that the construct should not change. Say
  the interval; a retest coefficient without one is uninterpretable.
- **Inter-rater**: Cohen's kappa for two raters and nominal categories,
  weighted kappa for ordinal, Krippendorff's alpha for any number of raters and
  missing data, ICC for continuous ratings. Kappa is depressed by rare
  categories — report the confusion matrix alongside it.
- **Attenuation**: unreliable measures bias correlations toward zero. Correcting
  for attenuation is legitimate but must be labelled and reported next to the
  uncorrected value.

## Common failures to check for

- Ceiling and floor effects — inspect the distribution before modelling.
- A single-item measure treated as if it had known reliability.
- Composite scores summed across items with different scales or missingness
  handling.
- Formative constructs (indicators cause the construct: SES from income,
  education, occupation) analysed with reflective tools like alpha. Alpha is
  meaningless for a formative index.
- A proxy relabelled as the construct in the abstract ("engagement" for clicks).

## What to report

Items, scoring, n, alpha/omega with intervals, factor structure and the sample
it was tested in, invariance results for any group comparison, and the plain
sentence naming what the score is evidence of and what it is not.

# Preregistration, replication and multiverse

## What a preregistration must pin down

Vague preregistration is worse than none because it buys credibility without
constraining anything. Fix, in writing, with a timestamp:

- Hypotheses, stated directionally, and which are confirmatory vs exploratory.
- Primary outcome and how it is scored from the raw items. One primary.
- Sample size and the stopping rule, with the power calculation and the effect
  it assumed (`power_analysis`, saved output).
- Exclusion rules — attention checks, timing, duplicates — decided before data.
- The exact analysis: model, covariates, transformations, how missing data is
  handled, one-sided or two-sided, alpha, and the multiplicity correction.
- What outcome pattern would count as the hypothesis failing.

Registries used in this field include OSF, AsPredicted and AEA RCT Registry;
check which one the target journal or funder expects. A registered report goes
further: peer review of the design before data collection, with in-principle
acceptance.

## The deviation log

Deviations happen and are fine. Keep a dated list: what changed, why, and the
result both ways. In the paper, label confirmatory and exploratory analyses
separately and never present an exploratory finding in confirmatory clothing.

## The practices that answer the replication crisis

- **Open data and materials** where consent and law allow; a de-identified
  analysis dataset plus the instrument plus the code is the standard package.
- **Open code**, run through `analysis_run` so the argv, environment and input
  hashes are recorded; `analysis_ledger(action="verify")` before submission.
- **Adequate power** planned in advance, or an honest statement that the study
  is a precise estimate exercise rather than a test.
- **Effect sizes with intervals** everywhere; `stats_effect_size`.
- **Direct replication** treated as publishable, and a replication that fails
  reported as a result rather than shelved.

## Multiverse and specification-curve analysis

When many defensible analytic choices exist (exclusions, covariates, outcome
coding, transformation), run them all and report the distribution of estimates
rather than the one you happened to pick. The output is: how many
specifications, what fraction point the same way, the median estimate, and where
the chosen headline specification sits in that distribution. This is the honest
answer to "the garden of forking paths", and it is cheap to run through
`analysis_run` once the pipeline is scripted.

## Reading other people's evidence

Ask: was the outcome preregistered, is the sample the one the claim is about,
what is the interval rather than the p-value, and does the effect size make
sense in real units. Small samples plus a surprising effect plus a p just under
0.05 is a pattern, not a discovery. Say "we do not know" when the literature is
a handful of underpowered studies with no replication.

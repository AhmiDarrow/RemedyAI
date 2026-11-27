# Test selection

Selection is determined by four questions, answered before any test name:

1. **Outcome type** — continuous, binary, ordinal, count, time-to-event,
   compositional.
2. **Design** — independent groups, paired/within-subject, repeated
   measures, clustered (schools, litters, sessions), purely observational.
3. **Number of groups / predictors** — one sample, two, k, or a model.
4. **What is being estimated** — a difference in means, a ratio, an
   association, a slope, a survival difference.

`stats_assumptions(...)` returns `recommended[]` with `why` and
`when_it_fails` — a proposal to check, not a verdict.

## Common map

| Design | Continuous | Binary | Ordinal | Count |
|---|---|---|---|---|
| 1 sample vs value | one-sample t | exact binomial | sign test | Poisson test |
| 2 independent | two-sample t (Welch) | chi-square / Fisher exact | Mann-Whitney | Poisson/NB regression |
| 2 paired | paired t | McNemar | Wilcoxon signed-rank | paired Poisson GLM |
| k independent | one-way ANOVA | chi-square independence | Kruskal-Wallis | NB regression |
| k repeated | mixed model | GEE / conditional logit | Friedman | mixed NB |
| association | Pearson r / regression | logistic regression | Spearman / ordinal logit | count regression |

**Default to Welch's t, not Student's.** Equal variances are an extra
assumption that buys almost nothing; Welch is the safer default at
essentially no cost when variances happen to be equal.

## The ladder when parametric assumptions look shaky

1. Parametric on the original scale, if the residuals behave.
2. Parametric on a **principled** transform (log for multiplicative
   processes and positive skew, logit for proportions) — chosen because
   the mechanism is multiplicative, not because it made the p smaller.
   Interpretation changes: a log-scale mean difference is a ratio.
3. **Rank-based** (Mann-Whitney, Wilcoxon, Kruskal-Wallis). These do not
   test means; Mann-Whitney tests stochastic dominance, and under unequal
   spreads it can be significant with identical medians. Pair it with
   Cliff's delta, not with Cohen's d.
4. **Permutation / bootstrap.** Usually the best answer: assumption-light,
   estimates what you actually meant, and gives an interval. Run it in the
   project env through `analysis_run` with a fixed seed recorded.
5. **Model the distribution** (GLM with the right family) rather than
   bending the data to fit a t-test.

## Refusals

- Do not run a t-test on clustered data as if the rows were independent —
  that is the single most common inflation of false positives. Use a mixed
  model or aggregate to the cluster and analyse cluster means.
- Do not run k pairwise tests instead of one model with a factor.
- Do not choose between paired and unpaired after looking at both.
- n below ~10 per group: report descriptives and the interval, and say
  the design cannot support an inferential claim.

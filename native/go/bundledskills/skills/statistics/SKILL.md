---
name: statistics
description: >
  Choose, run and report the right analysis: descriptives before inference,
  test selection driven by the design, assumptions checked with a statistic
  rather than assumed, effect sizes with intervals as the headline and
  p-values as support, multiplicity handled, power computed before data
  collection. Use whenever the task involves a test, a p-value, a sample
  size, a regression, or a claim that two groups differ.
version: 1.0.0
author: Remedy
tags: [statistics, inference, effect-size, power, regression, research]
requires: []
tools: [power_analysis, stats_assumptions, stats_effect_size, stats_multiplicity, data_profile, analysis_run, skill_activate, file_read, file_write]
triggers:
  - '\b(p[- ]?values?|confidence intervals?|effect sizes?|statistical(?:ly)?\s+(?:significan\w+|power))\b'
  - '\b(t-?test|ANOVA|chi[- ]?squared?|Mann[- ]?Whitney|Wilcoxon|Kruskal[- ]?Wallis|Fisher.?s exact)\b'
  - '\b(sample size|power analysis|a ?priori power|multiple comparisons?|Bonferroni|Benjamini|false discovery rate|\bFDR\b)\b'
  - '\b(mixed[- ]effects?|random effects? model|regression (?:coefficients?|assumptions?)|heteroscedastic\w*|collinearit\w+|multicollinearit\w+)\b'
---

# Statistics

`skill_activate(skill="research-method")` first, and do not restate the
spine here — that pack owns question framing, evidence standards,
preregistration, citation honesty and how to say "we do not know". This
pack owns only the analysis itself.

## Order of work (do not skip a step to get to the p-value)

1. **Write the estimand in one sentence** before touching a test: *what
   quantity, in what population, contrasted against what?* "Is there a
   difference?" is not an estimand. If the owner cannot answer it, ask —
   the test follows from it, not the other way round.
2. **Describe.** `data_profile(path, target=<outcome>)` → n, missingness,
   duplicates, cell sizes, class balance, oddities. Read the warnings and
   `leakage_suspects` before any model. A distribution you have not looked
   at will surprise you later.
3. **Select by design, not by habit** — `references/test-selection.md`.
   Between/within/paired, outcome type (continuous, binary, count, ordinal,
   time-to-event), independence structure. `stats_assumptions(data_path,
   outcome=, group=, design=)` returns `recommended[]` with *why* and
   *when_it_fails*, plus `fallbacks[]`.
4. **Check assumptions with a number.** Same call reports normality of
   residuals (D'Agostino-Pearson, refused below n=20 on purpose), variance
   homogeneity (Brown-Forsythe), outliers, balance, zero-inflation. When a
   check fails, `references/assumptions-and-diagnostics.md` says what to do
   about it — the answer is rarely "transform and carry on".
5. **Estimate before you test.** `stats_effect_size(kind=..., ...)` →
   estimate with CI. That pair is the result. Report the p as support.
6. **Handle multiplicity.** If the analysis implies more than one test,
   `stats_multiplicity(pvalues=..., labels=..., method="holm"|"bh")` and
   say what the family was.
7. **Power belongs before the data.** `power_analysis(test=, solve="n",
   effect_size=<the smallest effect worth detecting>)`. Post-hoc power is
   arithmetic on the p-value and carries no information — if the owner
   asks for it, give the confidence interval or the minimum detectable
   effect at the achieved n instead, and say why.
8. **Report the whole recipe**: n per cell, exclusions and why, the test,
   the estimate with its interval, the exact p, the software and versions.

## Hard rules

- Never pick or switch the test after seeing the p-value; never drop a
  covariate, an outlier or a subgroup because it moved significance. If
  you did try alternatives, all of them go in the report.
- Never dichotomise a continuous predictor or outcome to simplify a test —
  it throws away power and manufactures thresholds.
- "Not significant" is not "no effect". State what the interval fails to
  exclude, in the outcome's units.
- A significant result in one group and a non-significant one in another
  is not a difference between groups. Test the interaction directly.
- Report exact p values (`p = 0.032`), with `p < 0.001` as the floor.
  Never `p = 0.000`, never "approached significance".
- Effect-size labels (small/medium/large) are field conventions, not
  facts. Say what the number means in the outcome's own units.
- Sequential looks at accumulating data require a sequential design
  (alpha spending, group-sequential boundaries) fixed in advance.
  Otherwise stop at the planned n.
- Do not report an effect size, an interval or a p you have not seen a
  tool or a script produce. No number from memory or arithmetic in prose.

## What "verified" means here

A statistical claim is verified when **all** of these hold:

- The analysis reruns end to end from the raw file through
  `analysis_run(path=<script>)`, and `analysis_ledger(action="verify",
  run_id=...)` reports `INTACT` for the inputs and the artifacts.
- Every number in the write-up appears in that run's output. Grep the
  output for each figure in the text; a number that only exists in prose
  is unverified.
- Assumption checks are recorded next to the result, not asserted.
- At least one value is cross-checked by a second route — a closed-form
  hand computation, a second implementation, or a permutation/bootstrap
  version of the same test that lands in the same interval.

Any of these failing means the claim is not ready. Say so plainly.

## References

Read `references/INDEX.md`, then `file_read` what the task needs.

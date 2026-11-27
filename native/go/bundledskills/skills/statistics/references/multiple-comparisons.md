# Multiple comparisons

## Define the family first

The correction is meaningless until you say what family of tests it
covers. The family is every test that bears on the same claim: all
pairwise contrasts in one experiment, all outcomes for one hypothesis,
all subgroups you examined. Tests you ran and did not report are still in
the family. `stats_multiplicity` returns a `family_note` — fill it with
the truth.

## FWER vs FDR

- **FWER** (family-wise error rate) — probability of *any* false positive.
  Use when a single false claim is costly: a confirmatory trial endpoint,
  a safety signal, a go/no-go decision.
  - `bonferroni`: alpha/m. Simple, valid always, conservative.
  - `holm`: step-down, uniformly more powerful than Bonferroni, valid
    under any dependence. **Default choice for FWER.**
  - `hochberg`: step-up, more powerful still, but requires positive
    dependence (PRDS) or independence.
- **FDR** (false discovery rate) — expected proportion of false positives
  among the rejections. Use for screening and discovery: thousands of
  genes, voxels, features, where a controlled fraction of false leads is
  acceptable because everything gets followed up.
  - `bh` (Benjamini-Hochberg): independence or positive dependence.
  - `by` (Benjamini-Yekutieli): valid under arbitrary dependence, more
    conservative.

`stats_multiplicity(pvalues=..., labels=..., method=..., alpha=...)`
returns adjusted p-values per row and `n_rejected`. Report the adjusted
values alongside the raw ones, never instead of them.

## When correction is the wrong fix

- **A model instead of many tests.** k pairwise t-tests across groups is
  usually one ANOVA/mixed model with planned contrasts. Fit the model.
- **Hierarchical/gatekeeping designs.** A pre-specified order (primary,
  then key secondary only if primary passes) spends alpha without any
  correction at all — but the order must be fixed before data.
- **Composite or co-primary endpoints.** Deciding in advance that all of
  a set must move avoids the multiplicity entirely.
- **Partial pooling.** A hierarchical model shrinks extremes toward the
  group mean and handles multiplicity by design.
- **Exploratory work.** Say so, and label every p as descriptive.
  Correcting a sweep and reporting survivors as confirmed is the same
  error dressed up.

## The honesty rule

Adjusting after seeing the results is not the same as planning them.
Correction fixes the arithmetic of a pre-specified family; it cannot undo
choosing which comparisons to look at because of what the data showed. If
the family was assembled post hoc, the report must say the analysis is
exploratory and needs an independent replication — and the `family_note`
must say it too.

## Reporting

State: how many tests, what the family was, the method, alpha, and the
adjusted values. If some comparisons were pre-registered and others were
not, split them into two tables. Never report only the survivors.

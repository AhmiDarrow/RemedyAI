# Analysis plan (written before the data)

The plan is a document, written at design time, that a second analyst could
follow to the same numbers. If a decision is not in it, the decision is
exploratory.

## The slots

1. **Dataset** - source, version/date, inclusion and exclusion at the row
   level, and the expected n after each filter.
2. **Primary outcome** - one. Its definition, instrument, scale, direction,
   and the timepoint.
3. **Secondary outcomes** - listed and ranked. They do not get promoted later.
4. **Estimand** - the quantity you want: difference in means at week 12 in
   everyone randomised, hazard ratio over follow-up, accuracy on a held-out
   split. Say it in words before naming a test.
5. **Model / test** - the specific procedure, one- or two-sided, alpha, and
   the covariates in the model (prespecified, not stepwise-selected).
6. **Assumptions and what happens if they fail** - the fallback named in
   advance ("if residuals are strongly skewed, log-transform; if that fails,
   a rank-based test"). A branch chosen after seeing the p-value is a
   forking path.
7. **Missing data** - mechanism assumed, complete-case vs imputation, and how
   much missingness would make the analysis unreliable.
8. **Outliers and exclusions** - a numeric rule ("> 3.5 MAD from the median,
   evaluated blind to condition"), applied before unblinding.
9. **Multiplicity** - how many tests the plan implies, the family, and the
   correction (`stats_multiplicity`). Decided now; adjusting after seeing the
   results is not the same as planning it.
10. **Stopping rule** - fixed n, or the sequential boundaries.
11. **Sensitivity analyses** - pre-named alternatives that check whether the
    conclusion depends on a judgement call.
12. **Exploratory section** - everything else, declared as such up front.

## Blind analysis

Where the field supports it: build and debug the whole pipeline on shuffled
labels, simulated data, or a blinded subset, freeze the code, then unblind
once. The analysis cannot be tuned toward the answer while the answer is not
visible.

## Dry-run the pipeline

Before real data: generate a synthetic dataset with the planned shape, run the
whole plan end to end through `analysis_run`, and confirm it produces the
tables and figures. Bugs found here are free, and the run id is proof the plan
was executable before the data existed.

## Confirmatory vs exploratory in the write-up

Two sections, or two marked columns in every table. Exploratory results get
effect sizes and intervals, are described as hypothesis-generating, and never
take a confirmatory verb ("shows", "demonstrates", "establishes").

## Analysis code discipline

- Raw data is read-only. Every transformation is code, never a spreadsheet
  edit.
- One script per stage (clean -> derive -> model -> figures), each runnable
  alone, each recorded in the ledger.
- Seeds set and recorded for anything stochastic.
- Numbers reach the manuscript from generated files, never hand-typed.

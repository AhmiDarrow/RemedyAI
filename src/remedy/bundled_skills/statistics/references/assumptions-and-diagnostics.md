# Assumptions and diagnostics

`stats_assumptions` computes each check and returns a statistic, a value,
a p where one exists, and a verdict. Read the statistic, not just the
verdict.

## What each assumption actually is

- **Normality** applies to the *residuals*, never to the raw outcome.
  With n in the hundreds the CLT usually carries the mean; below n=20 the
  test has no power and `stats_assumptions` refuses it
  (`code=N_TOO_SMALL`) rather than emitting a meaningless p; at large n it
  flags trivial departures. The QQ shape and the design matter more.
- **Homogeneity of variance** — Brown-Forsythe (Levene on medians) is the
  robust form. If it fails, use Welch rather than transforming.
- **Independence** is **not testable from a table**. It is a design fact:
  who was measured more than once, what shares a batch, a plate, a
  classroom, a session, an operator, a day. The tool flags it as a
  question to put to the owner. Ask it explicitly and record the answer.
- **Linearity** (regression) — residual-vs-fitted plot; curvature means
  the mean model is wrong, and no amount of robust SE fixes a wrong mean
  model.
- **Equal-interval / measurement** — Likert items are ordinal; means over
  a validated multi-item scale are defensible, a mean of one 5-point item
  is not.

## When a check fails

| Failure | Do this | Do not |
|---|---|---|
| Skewed residuals | permutation/bootstrap, GLM with the right family, or a mechanism-justified transform | fish for the transform with the best p |
| Unequal variances | Welch t / Games-Howell; report both SDs | Student's t plus a hopeful note |
| Outliers | run with and without, report both; investigate the rows | delete silently, or winsorise without saying so |
| Heteroscedasticity in regression | robust (HC3) SE, or model the variance | ignore it |
| Zero-inflated counts | hurdle / zero-inflated model | log(x+1) then a t-test |
| Clustering | mixed model, GEE, or cluster-robust SE | more n at the row level |
| Missing data | describe the mechanism, then complete-case *with a stated assumption* or multiple imputation | mean-imputation |

## Outliers

Report the rule before applying it. IQR fence (1.5x) and MAD-z (|z|>3.5)
are conventions; MAD is the more robust. An outlier is a data question
first — instrument fault, entry error, or a real tail. Removing a real
tail changes the estimand and must be stated.

## Diagnostics to actually look at

Residual vs fitted, QQ of residuals, scale-location, Cook's distance /
leverage, residual vs each predictor, and residual vs run order when the
data were collected in sequence. Generate them through `analysis_run` so
the figures land in the ledger with the data that produced them.

## Sample-size honesty

Every check is itself a test, with its own power. A passed assumption
check at n=15 is not evidence the assumption holds; it is evidence the
check could not detect a violation. Say that.

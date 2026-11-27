# Baselines and ablations

## A baseline is an adversary, not a formality

Build these, in order, and report all of them:

1. **Trivial** — majority class / mean predictor / persistence (predict
   the last value). This is the floor, and it is embarrassing how often it
   is competitive. It also tells the reader the base rate.
2. **Simple classical** — logistic or linear regression, and gradient-
   boosted trees, on the same features. On tabular data these frequently
   win; a deep model that does not beat them has no result.
3. **Prior work** — the method you are claiming to beat, **re-run by you**
   on your split, with the same preprocessing and metric implementation,
   and tuned with the same budget as your method. Quoting a number from a
   paper that used a different split, tokeniser or metric is not a
   comparison; if you truly cannot re-run it, label the row "reported by
   [cite]" and say the comparison is not like-for-like.
4. **Your method with the interesting part removed** (see ablations).

Equal-budget is the rule that makes comparison meaningful: the same
number of trials in the hyperparameter search, the same seed count, the
same compute or wall-clock cap, the same data. Record all of it. Use
`lit_search` / `cite_add` for the prior method's exact configuration, and
say when the paper does not specify something you had to choose.

## Ablations

An ablation answers *which part of the system produces the gain*. It only
does that when exactly one thing changes.

- Ablate **from the full system downward** (remove component X), not from
  the baseline upward — additive studies interact and mislead.
- Same seeds, same budget, same data, same evaluation code for every row.
- Report the seed spread per ablation row. A 0.3-point drop with a
  0.8-point seed SD is not evidence the component matters.
- Include the ablations that did **not** hurt. A component you kept
  because it felt right, with a null ablation, is reported as null.
- Beware confounded ablations: removing a component often changes the
  parameter count, the effective learning rate, or the training time.
  Control for that (match parameters, re-tune the ablated variant) and
  say which control you applied.
- Ablate the data too — training set size, augmentation, filtering steps.
  A learning curve over dataset fraction often explains more than an
  architecture table.

## Negative controls

Two cheap checks that catch broken pipelines:

- **Shuffled labels** — retrain on permuted labels. Performance must fall
  to chance. If it does not, there is leakage or an evaluation bug.
- **Ablate the input** — feed noise or a constant. Same expectation.

Run both once per project and record the run ids; they are the cheapest
protection against a whole line of work resting on a bug.

## Reporting

The table carries: method, budget (trials, epochs, compute), seeds, mean
and spread, and the delta to the strongest baseline with an interval.
Bold nothing that is inside the seed spread.

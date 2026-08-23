---
name: ml-research
description: >
  Empirical machine-learning claims that survive scrutiny: a baseline
  strong enough to be worth beating, splits that respect grouping and
  time, leakage hunted before any number is trusted, ablations that
  isolate one change, results reported as a spread over seeds rather than
  a single number, and benchmark deltas tested instead of eyeballed. Use
  when the task is measuring whether a model or a change actually works.
version: 1.0.0
author: Remedy
tags: [machine-learning, research, evaluation, benchmarking, reproducibility]
requires: []
tools: [data_profile, data_diff, analysis_run, analysis_ledger, analysis_env, stats_effect_size, stats_multiplicity, lit_search, cite_add, skill_activate]
triggers:
  - '\b(train(?:ing)?[/ -]?(?:test|val|validation)[/ -]?split|cross[- ]?validat\w+|hold[- ]?out set|data leakage)\b'
  - '\b(hyper-?parameter (?:search|tuning|sweep)|ablation (?:study|table|experiment)|learning curves?)\b'
  - '\b(ROC[- ]?AUC|\bAUROC\b|F1 scores?|precision[/ ]recall|calibration (?:curve|error)|\bECE\b)\b'
  - '\b(random seeds?|seed variance|overfit\w*|generali[sz]ation gap|model card|benchmark leaderboard)\b'
---

# ML research

`skill_activate(skill="research-method")` first, and do not restate the
spine — that pack owns question framing, evidence standards,
preregistration, citation honesty and "we do not know". This pack owns
what is different about *research on models*: the claim is empirical, the
measurement is noisy, and almost every published failure is a
measurement failure, not a modelling one.

Shipping a model is a different job. Here the deliverable is a **claim**
("X improves Y by Z under conditions C") and the evidence that supports
it.

## Order of work

1. **Write the claim and its falsifier first.** "Method A beats B on
   task T by more than the seed spread, under an equal tuning budget."
   If you cannot say what result would refute it, the experiment is not
   designed yet.
2. **`analysis_env(path)`** — what runs here, which frameworks and
   versions, is there a GPU, is pandas/numpy importable in the project
   env. Everything after this goes through `analysis_run` so the argv,
   input hashes, artifacts and duration land in the ledger.
3. **`data_profile(path, target=<label>)`** before any training. Read
   `leakage_suspects` and the class balance. Duplicated rows across a
   split are the most common way a paper's headline number is wrong.
4. **Split before you look** — `references/splits-and-leakage.md`. Group
   by the unit that must not straddle the boundary (patient, user,
   document, session, molecule scaffold); split by time when deployment
   is forward in time. Freeze the test set and touch it once.
5. **Build the honest baseline** — `references/baselines-and-ablations.md`.
   Majority class, a linear/gradient-boosted model on the same features,
   and the prior work's method *tuned as hard as yours*. A baseline you
   did not tune is not a baseline.
6. **Run with N seeds, not one** — `references/variance-and-seeds.md`.
   Report mean and spread (SD or min-max over seeds), never a single
   number, and never the best seed.
7. **Ablate one thing at a time** from the full system, each with the
   same seeds and budget.
8. **Evaluate deliberately** — `references/evaluation-design.md`: the
   metric that matches the decision, calibration, subgroup breakdowns,
   and a real test of the delta (`stats_effect_size`, paired across seeds
   or bootstrap over the test set; `stats_multiplicity` when many
   variants are compared).
9. **Report** — `references/benchmark-reporting.md` and
   `references/reproducibility-checklist.md`.

## Hard rules

- The test set is touched **once**, at the end. Every choice — features,
  architecture, early stopping, threshold, prompt — is made on validation.
  If you looked at test, it is now a validation set; say so and get new
  held-out data.
- Never report a single run. A number without a spread is not a result.
- Never compare your tuned method against an untuned baseline, or at a
  different compute budget, and never quote a prior number obtained on a
  different split, preprocessing or metric implementation. Rerun it, or
  label the comparison as not like-for-like.
- Accuracy on an imbalanced problem is not a result. State the base rate
  next to every headline metric.
- No number in the write-up that did not come out of a recorded run.
  `analysis_ledger(action="verify", run_id=...)` must say `INTACT`.
- Negative and null results get reported with the same detail as
  positive ones.

## What "verified" means here

An ML claim is verified when:

- The full pipeline reruns from raw data to the reported table through
  `analysis_run`, on a clean checkout, from recorded seeds, and
  `analysis_ledger(action="verify")` reports `INTACT` for inputs and
  artifacts.
- Independent seeds reproduce the effect, and the reported interval /
  paired test over seeds excludes zero.
- A leakage audit ran and is documented (`data_diff` between train and
  test on identifiers and near-duplicates; `data_profile` suspects
  resolved one by one).
- The baseline was re-run by you under the same budget, not cited.

Say plainly which of these did not happen. "It works on our split" is a
statement about the split.

## References

Read `references/INDEX.md` first, then open the reference the task needs.

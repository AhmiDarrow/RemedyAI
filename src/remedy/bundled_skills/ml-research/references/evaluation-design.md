# Evaluation design

## The metric comes from the decision

Ask what action the prediction drives and what each error costs. Then:

- **Imbalanced binary** — accuracy is useless; state the base rate. Use
  precision/recall or average precision (area under PR) when positives
  are rare and the negatives are uninteresting. ROC-AUC is insensitive to
  prevalence, which is a feature when comparing across populations and a
  trap when the deployed prevalence is 0.1%.
- **F1** hides the trade-off it averages and depends on the threshold.
  Report precision and recall too, at a stated operating point.
- **Ranking / retrieval** — nDCG, MRR, recall@k, with k justified by the
  interface.
- **Regression** — RMSE punishes tails, MAE does not; R² depends on the
  variance of the sample and is not comparable across datasets.
- **Multi-class** — say macro vs micro vs weighted averaging. Macro
  treats a 20-example class equally with a 20 000-example one, which may
  or may not be what you want.
- **Generative / open-ended** — overlap metrics (BLEU, ROUGE) correlate
  weakly with quality. Where a human or model judge is used, report the
  rubric, the number of raters and inter-rater agreement, note that a
  model judge favours its own family, and validate it against human
  labels on a subsample.

Fix the threshold on validation, never on test, and report the metric at
that fixed threshold as well as the threshold-free number.

## Calibration

Discrimination and calibration are different properties; a model can rank
perfectly and still be badly over-confident. Report a reliability diagram
and expected calibration error (with the binning scheme stated — ECE is
sensitive to it), plus a proper scoring rule (Brier, log loss) which
measures both at once. If the model feeds a threshold or a cost
calculation, calibration is part of the claim, not an extra.

## Slices, not just the mean

Report performance per subgroup that matters: class, source, device,
language, time period, difficulty band, and any protected attribute the
governance framework requires. A gain that comes entirely from the
majority slice is a different claim from a uniform gain. Small slices
carry wide intervals — show them. Where the data concerns people,
subgroup reporting rides on the same consent and de-identification terms
as the rest of the dataset; do not infer a new attribute to slice on.

## Uncertainty on the metric

Two independent sources, reported separately:

1. **Test-set size** — bootstrap over test examples (resample with
   replacement, recompute the metric, take a percentile interval). This
   tells you whether a 0.4-point gap on 500 examples means anything.
2. **Training randomness** — spread over seeds (see variance-and-seeds).

For paired model comparison on the same test set, bootstrap or permute
the *per-example differences*, not the two metrics independently — the
models see identical examples and the paired interval is much tighter.
McNemar's test is the classical choice for paired binary correctness.

## Do not

Tune on test; report the best checkpoint chosen by test; change the
metric after seeing results; average benchmarks with different scales
into one headline number without saying how.

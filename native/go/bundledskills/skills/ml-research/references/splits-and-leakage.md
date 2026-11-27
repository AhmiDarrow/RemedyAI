# Splits and leakage

## Choose the split from the deployment question

- **Random i.i.d.** only when rows really are exchangeable and no unit
  appears twice.
- **Grouped** whenever rows share a unit that must not straddle the
  boundary: patient, subject, user, session, document, source site,
  device, molecular scaffold, images from the same visit. Split on the
  group id, then check the id sets are disjoint.
- **Temporal** whenever the model will run forward in time. Train on the
  past, validate on a later window, test on the latest. Rolling-origin
  (walk-forward) CV instead of k-fold. Random splits on time series
  overstate performance badly.
- **Stratified** on the label (and on any small critical subgroup) to keep
  rare classes present in every fold.
- **Nested CV** when hyperparameters are tuned and you also want an
  unbiased estimate: inner loop tunes, outer loop estimates. Tuning on the
  same folds you report is optimistic by a margin that can exceed the
  effect you are claiming.

Freeze the test set once, write its hash into the ledger, and touch it at
the end. Record the split code and seed — a split you cannot regenerate
makes every later number unverifiable.

## The leakage taxonomy

1. **Duplicate / near-duplicate rows** across splits. Check exact hashes,
   then near-duplicates (normalised text, perceptual hash, fuzzy join on
   key fields). Use `data_diff(left=train, right=test, key=<id>)` and read
   `key_overlap`.
2. **Preprocessing fitted on all the data** — scalers, imputers, PCA,
   vocabulary, target encoding, feature selection, SMOTE. Every fit
   belongs inside the CV fold, on train only. Pipeline objects exist for
   this reason.
3. **Target leakage in features** — a column recorded after or because of
   the outcome (discharge code, "days_until_churn", a post-treatment
   measurement, an id assigned in outcome order). `data_profile` reports
   these as *suspects* with evidence; resolve each by asking when the
   value was recorded relative to prediction time.
4. **Temporal leakage** — a feature computed with a window that reaches
   past the prediction timestamp; a join on a table later updated in
   place. Ask of each feature: would this have been available at
   inference time?
5. **Group leakage** — the same subject in train and test (see above).
6. **Test-set contamination** — the benchmark appears in a pretraining
   corpus. Check for verbatim overlap between test items and training
   data; report the check and its limits, and say plainly when you cannot
   inspect the pretraining data.
7. **Selection into the dataset** — labels only exist for rows that passed
   some earlier filter. That filter is part of the model's population.

## The tell

A metric dramatically better than the literature, near-perfect, or a
single feature carrying almost all the signal, is leakage until proven
otherwise. Drop the suspect feature and re-run; if the result collapses,
you found it.

Document the audit: which checks ran, what they found, what remains
unverifiable.

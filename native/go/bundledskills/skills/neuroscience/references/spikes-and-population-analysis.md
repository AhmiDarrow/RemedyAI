# Spikes, PSTHs, decoders, population geometry

## Sorting comes first

Report the sorter and version (Kilosort, MountainSort, manual curation) and
the quality metrics with their thresholds: ISI violation rate, presence
ratio, amplitude cutoff, SNR, drift. State whether units are single or multi
— contaminated multi-unit changes every population claim. On chronic arrays,
demonstrate unit stability across days (waveform, ISI, cross-correlogram)
before treating a channel as the same cell.

## PSTHs and tuning

- Bin width and smoothing kernel set the temporal resolution of the claim;
  state them.
- Fix the baseline window a priori; a baseline estimated from few trials
  manufactures apparent modulation.
- Trial counts differ across conditions more often than noticed, and many
  spike statistics are count-dependent; match or model.
- Selecting cells for being "responsive" and then reporting their response
  is circular (see statistical-traps.md). Select on an independent criterion
  or report the whole population.
- Trial-to-trial dependence and slow drift mean trials are not independent;
  permuting condition labels within blocks beats a t-test over pooled
  trials.
- The unit of analysis for a population claim is usually the session or the
  animal, not the neuron. Pooling neurons across animals as independent
  samples inflates n badly; use a hierarchical model or aggregate.

## Decoding

- Cross-validate with a split that respects the dependency structure:
  leave-one-session-out, or blocked folds in time. Random trial folds leak
  when trials are correlated in time or share a block.
- Every choice made using test data leaks: feature and channel selection,
  hyperparameters, preprocessing fitted on all trials, stopping when it
  looks good. Fit inside the fold.
- Compare against a null built by permuting labels through the same
  cross-validation, not against 1/n_classes — chance is not 50% when classes
  are imbalanced or trials are correlated.
- Report intervals over folds/sessions and the confusion matrix.
  Above-chance decoding shows information is *present and linearly
  available*, not that the area uses it.
- Accuracy depends on neuron and trial counts; comparing areas requires
  matching both or subsampling.

## Dimensionality reduction and population geometry

PCA, factor analysis, dPCA, jPCA, CCA, UMAP/t-SNE. Each finds structure in
noise if asked to. Rules:

- Choose components by a criterion stated in advance (cross-validated
  reconstruction error, or a shuffle control), not by the picture.
- Fit on training data and project held-out data; a manifold fitted on
  everything describes, it does not predict.
- Nonlinear embeddings (t-SNE, UMAP) distort global distances and depend on
  perplexity settings — they are visualisations, and cluster separation in
  them is not evidence of discrete classes.
- Neural "trajectories" can come from smoothing and condition-averaging
  alone; run a shuffled control before claiming dynamics.

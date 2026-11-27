# Preprocessing: every choice moves the result

Pipelines are forking paths with defaults. Pre-specify the pipeline, run it
through `analysis_run` so the ledger holds the argv, versions and input
hashes, and report it well enough to rerun. If you compare pipelines, show
the spread — an honest multiverse beats one arm reported silently.

## fMRI

- **Motion.** The largest confound, and it correlates with age, clinical
  status and arousal — so motion differences pose as group effects. Compute
  framewise displacement and DVARS, pre-specify a threshold and a minimum
  surviving volume count, and report excluded volumes and subjects per
  group. Options: 24-parameter regression, scrubbing, spike regressors,
  aCompCor, ICA-AROMA. Post-hoc threshold shopping is p-hacking.
- **Slice timing** matters for event-related designs with long TRs and
  interacts with motion correction; state the order used.
- **Susceptibility distortion**: fieldmap or opposite-phase-encode
  correction, else frontal and temporal signal is displaced and any
  localisation there is soft.
- **Registration and normalisation**: EPI to T1 to template (name the MNI
  variant), or surface-based via FreeSurfer. Inspect registrations — a
  failed one produces beautiful, wrong group maps.
- **Smoothing** trades resolution for sensitivity and interacts with
  cluster-based inference; the kernel is part of the result.
- **Global signal regression** shifts correlations negative and changes
  group differences; report with and without.
- fMRIPrep is a defensible default: it emits a versioned report and confound
  table. Pin the version (containers) and archive the reports.

## EEG/MEG

- **Filtering**: a high-pass cutoff above ~0.1-0.5 Hz distorts slow
  components and can manufacture pre-stimulus effects; steep filters ring in
  time. Report filter type, cutoffs, order and direction (zero-phase or
  causal), and do not filter in a way that leaks post-stimulus data into the
  baseline.
- **Referencing** changes topography and every downstream statistic. Average
  reference needs adequate coverage; a mastoid or REST reference is a
  different analysis. State it.
- **Epoching**: baseline window chosen a priori; baseline correction can
  push effects into the pre-stimulus period.
- **ICA / artefact rejection**: remove components by a documented rule
  (template correlation, automated classifier), counted and reported. Manual
  removal by eye must be blind to condition.
- **Interpolated** bad channels are reported with their count, and the
  detection threshold is pre-specified.
- Trial counts must be matched or modelled across conditions — unequal
  counts change signal-to-noise and mimic effects.

## Imaging and ephys

Frame-series motion correction, ROI segmentation, neuropil subtraction
factor, dF/F baseline, deconvolution parameters; for ephys, filter bands for
spikes vs LFP, common-average referencing, and the sorter with its version
and parameters. All of it is part of the result.

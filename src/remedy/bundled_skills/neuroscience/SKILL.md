---
name: neuroscience
description: >
  Brain data from acquisition to inference: what each modality actually
  measures (BOLD haemodynamics, EEG/MEG timing, spikes and LFP, calcium
  kinetics, optogenetic controls), BIDS layout and NIfTI/EDF/NWB formats,
  preprocessing choices that move results, and the traps the field is famous
  for — circular ROI selection, double dipping, voxel-wise multiple
  comparisons, cluster-extent inflation, tiny-n designs, overfitted decoders
  and connectivity claims. Use for fMRI, EEG/MEG, electrophysiology,
  imaging or any analysis of neural recordings.
version: 1.0.0
author: Remedy
tags: [research, neuroscience, fmri, eeg, electrophysiology, imaging, statistics]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_profile, stats_assumptions, stats_multiplicity, power_analysis, lit_search, cite_add, skill_activate]
triggers:
  - '\b(fMRI|\bEEG\b|\bMEG\b|BOLD signal|diffusion MRI|\bDTI\b|resting[- ]state)\b'
  - '\b(spike (?:sorting|trains?)|local field potentials?|patch[- ]clamp|optogenetic\w*|two[- ]photon|calcium imaging)\b'
  - '\b(cluster[- ](?:wise|level) correction|voxel[- ]wise|whole[- ]brain correction|\bROI\b analysis|double dipping|circular analysis)\b'
  - '\b(fMRIPrep|FreeSurfer|SPM12|\bFSL\b|AFNI|MNE-?Python|NIfTI|BIDS dataset)\b'
---

# Neuroscience (neural data and its inferences)

Run `skill_activate(skill="research-method")` first and work off that spine —
question framing, evidence, preregistration, citation honesty, "we do not
know". This pack covers only what neural data adds.

## The two sentences that prevent most of the damage

1. **Say what the signal is.** BOLD is a haemodynamic proxy lagging neural
   activity by seconds; EEG/MEG is millisecond-resolved summed postsynaptic
   current with an ill-posed source problem; a spike is one cell's output;
   calcium fluorescence is a filtered, nonlinear spike proxy; LFP is local
   input and processing, not output. Write the claim in the units the method
   actually delivers. `references/modalities-and-what-they-measure.md`.
2. **Every selection step must be independent of the effect being tested.**
   Picking voxels, channels, time windows, ROIs, cells or trials by the
   contrast you then test is circular, and the resulting statistic is not
   interpretable. `references/statistical-traps.md`.

## Decision tree

1. `analysis_env(path)` — what runs here (Python/MNE, FSL, SPM, AFNI,
   FreeSurfer, fMRIPrep, R, MATLAB). Nothing is verified on a machine that
   has none of it; say so rather than describing a pipeline you cannot run.
2. **Layout the data as BIDS** before analysing, and validate it. Raw stays
   read-only; derivatives live under `derivatives/<pipeline>`. Formats,
   sidecars and the metadata that is easy to lose (TR, slice timing,
   PhaseEncodingDirection, reference electrode, sampling rate, units):
   `references/bids-and-data-formats.md`.
3. **Preprocess deliberately.** Every choice — motion handling, slice
   timing, filter cutoffs, referencing, ICA rejection, normalisation,
   smoothing kernel, denoising — changes the result. Record the exact
   pipeline version and settings; run it through `analysis_run` so it lands
   in the ledger with input hashes. `references/preprocessing-choices.md`.
4. **Size the study honestly.** `power_analysis` on the effect that matters,
   in the units of the second-level test (subjects for group inference,
   cells or sessions for within-animal). Small-n neuroimaging does not
   produce small effects — it produces inflated, unstable ones. Trials per
   condition and subjects are different axes; more trials do not rescue
   too few subjects for a group claim.
5. **Correct across the whole search space.** Voxels, vertices, channels,
   time points, frequency bins, ROIs, contrasts. `stats_multiplicity` for
   discrete families; permutation/TFCE for images.
   `references/multiple-comparisons-imaging.md`.
6. **Spikes and populations**: PSTHs, decoders and dimensionality reduction
   each overfit in their own way. `references/spikes-and-population-analysis.md`.
7. **Connectivity**: correlation is not communication.
   `references/connectivity-claims.md`.
8. **Anchor on behaviour.** A neural difference with no behavioural or
   stimulus anchor, and no control condition, is a difference in the
   recording. `references/behaviour-and-controls.md`.

## Hard rules

- Report the full pipeline: sequence/acquisition parameters, software and
  version, every preprocessing step, the exclusion criteria and **how many
  subjects, sessions, trials and cells were excluded and why** — decided
  before looking at the effect.
- Report the correction method, the search space it covered, the
  cluster-forming threshold if any, and the corrected p. "p < 0.001
  uncorrected" is a description of a picture, not a result.
- Report effect sizes with intervals, not only peak coordinates and t-values
  — a peak selected for being the peak is biased upward.
- Unthresholded statistical maps go with the paper (NeuroVault); code and
  the BIDS derivative names go in the methods.
- Animal work runs under IACUC/institutional approval, and the approval, the
  species, strain, sex, age and housing appear in the methods. Human
  recordings carry the consent, ethics approval and de-identification
  conditions (defacing structurals, stripping scanner and EDF headers) —
  activate `clinical-research` when the study involves patients.

## How a claim gets verified here

The pipeline reruns from raw BIDS through `analysis_run` and reproduces the
figure; `analysis_ledger(action="verify", run_id=...)` reports INTACT for
inputs and artifacts; the statistic survives its own correction over the
real search space; the effect holds in data not used to select it — a held-
out run, a split half, or a preregistered replication sample.

Read `references/INDEX.md` and pull what you need with `file_read`.

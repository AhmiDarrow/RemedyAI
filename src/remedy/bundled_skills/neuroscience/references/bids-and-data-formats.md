# BIDS layout and data formats

## Why BIDS first

The Brain Imaging Data Structure fixes filenames, folders and sidecar
metadata so pipelines find data without bespoke glue. Specification and the
validator are published by the BIDS community (bids.neuroimaging.io); check
the current version and its modality extensions (MRI, EEG, MEG, iEEG, PET,
microscopy, NIRS) rather than trusting an example from memory.

Shape:

```
dataset_description.json  participants.tsv  README  CHANGES
sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz (+ .json)
                func/sub-01_ses-01_task-nback_run-1_bold.nii.gz (+ .json)
                     sub-01_ses-01_task-nback_run-1_events.tsv
                eeg/ sub-01_ses-01_task-nback_eeg.edf (+ .json, channels.tsv)
derivatives/fmriprep-<version>/...
```

Rules that matter: raw is read-only; every derivative goes under
`derivatives/<pipeline>-<version>/` with its own `dataset_description.json`;
entities appear in fixed order; `events.tsv` needs `onset` and `duration` in
seconds. Run the validator before analysis and paste the result.

## The metadata that gets lost

Losing these makes an analysis unreproducible, and they are exactly what
conversion tools drop:

- **fMRI**: `RepetitionTime`, `EchoTime`, `SliceTiming`,
  `PhaseEncodingDirection`, `TotalReadoutTime` (needed for distortion
  correction), multiband factor, flip angle, scanner and coil.
- **EEG/MEG/iEEG**: sampling rate, **reference** and ground, filters applied
  at acquisition, channel units and types, line frequency, electrode
  coordinates and their coordinate system, event trigger codes and their
  latency offset.
- **Ephys/imaging (NWB)**: units, gain, probe geometry, indicator and
  promoter, frame rate, and the timestamp clock relating streams.

## Formats

- **NIfTI (.nii/.nii.gz)** — the imaging workhorse; the affine defines
  voxel-to-world mapping and orientation. Never assume left-right;
  mislabelled orientation is a classic silent error. `.gz` everything.
- **DICOM** — scanner output, full of identifiers; convert (dcm2niix) into
  BIDS and keep the conversion log.
- **EDF/EDF+ / BrainVision (.vhdr/.vmrk/.eeg) / FIF** — electrophysiology
  containers; EDF headers carry patient fields that must be cleared.
- **NWB** — self-describing HDF5 for ephys and imaging, holds raw, processed
  and metadata together; good archival target (DANDI).
- **GIFTI/CIFTI** — surface and grayordinate data (HCP-style pipelines).
- Tabular sidecars are TSV with a JSON data dictionary; `data_profile` reads
  `participants.tsv` and `events.tsv` for missingness and coding errors
  before they become a modelling bug.

## De-identification

Deface or skull-strip structurals (pydeface, mri_deface) and check the
result visually; strip DICOM and EDF header fields; remove dates finer than
year and any scanner-assigned patient id; watch that a rare condition plus
site plus age re-identifies. Share through a repository that supports the
data-use terms your ethics approval sets.

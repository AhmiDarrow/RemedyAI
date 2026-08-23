# Spectra: interpretation workflows

Order matters: formula first, then connectivity, then stereochemistry. One
technique alone rarely proves a structure.

## Mass spectrometry

- **Nominal vs accurate mass**: only HRMS constrains a formula. Report
  calculated and found m/z, the ion form ([M+H]+, [M+Na]+, [M-H]-), and the
  accuracy in ppm against the instrument specification.
- **Isotope patterns** identify heteroatoms: Cl (about 3:1 M/M+2), Br (about
  1:1), S, Si, B, and the carbon-count trend in M+1. Compare the measured
  pattern against the simulation for the proposed formula.
- Adducts, in-source fragmentation, dimers and multiply-charged ions all
  masquerade as the molecular ion. Confirm in a second ionisation mode when
  the answer matters.
- Formats: **mzML** (HUPO-PSI) is the archival format; convert vendor files
  with the supported converter and keep the raw file.

## NMR

- Report solvent, reference, frequency and temperature. Shifts are solvent-
  and concentration-dependent; residual solvent peaks are the usual internal
  reference.
- 1H: integrate before assigning. Integrals give proton counts and must sum
  correctly; multiplicity and J give connectivity. Report as shift
  (multiplicity, J in Hz, integral, assignment).
- 13C: count unique carbons against symmetry. Quaternary carbons go missing
  at short relaxation delays — never report a carbon you did not see.
- 2D when 1D is ambiguous: COSY (H-H), HSQC (one-bond C-H), HMBC (2-3 bond
  C-H, builds the skeleton), NOESY/ROESY (through space, relative stereo).
  Assigning a new compound without 2D is a claim, not evidence.
- Impurity and solvent peaks have published tables — check them before
  inventing a signal.
- Predicted spectra are predictions. Never present one as measured data.

## IR and other

IR confirms functional groups (carbonyl region, O-H/N-H stretches) and is
weak evidence of a skeleton. UV-Vis gives chromophore and, via
Beer-Lambert, concentration — state path length and molar absorptivity.
Optical rotation needs concentration, solvent, temperature and wavelength,
and compares only against literature measured the same way.

## Formats and processing

**JCAMP-DX** (IUPAC) is the portable spectral exchange format; vendor formats
hold the raw FID and the acquisition parameters — archive those, not only the
picture. Processing choices (apodisation, zero-filling, phasing, baseline
correction, peak-picking threshold) change integrals; record them, and
process a comparison series identically.

Peak lists are ordinary tabular data — `data_profile` reads a CSV export, and
processing scripts run through `analysis_run` so the parameters land in the
ledger with the output.

## What the journal wants

For a new compound, most journals expect 1H and 13C with full assignments,
HRMS with calculated and found masses, purity evidence, and copies of the
spectra in the supporting information. Check the target journal's current
author guidelines — requirements differ and change.

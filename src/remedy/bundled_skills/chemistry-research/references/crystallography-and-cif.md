# Crystallography and CIF

## The CIF

The Crystallographic Information File is the IUCr standard: tagged
name/value pairs and loops holding cell, symmetry, atom sites, displacement
parameters, geometry and the experimental description. It is plain text —
read it directly, and treat the tags as the source of truth rather than a
summary table someone typed.

What must be present for a structure report: unit cell and errors, space
group, temperature, radiation and wavelength, crystal description, data
collection range and completeness, absorption correction, reflections
(measured, unique, observed), R_int, refinement method, restraints and
constraints, R1/wR2 (for observed and for all data), goodness of fit, largest
difference peak and hole, and the deposition number.

## Reading the quality numbers

- **R1 / wR2** measure agreement between model and data. Low R1 with a
  chemically absurd model is possible; the number is necessary, not
  sufficient.
- **GooF** near 1 indicates the weighting scheme matches the residuals.
- **R_int** describes the merging consistency of equivalent reflections.
- **Completeness and resolution** bound what the data can support. High-angle
  data missing means light atoms and displacement parameters are less
  determined.
- **Residual density** far above the noise near a heavy atom or a solvent
  cavity means unmodelled electron density — say so rather than ignoring it.
- Data-to-parameter ratio, restraints used, and any disorder model, twin law
  or solvent mask (squeeze-type) must be stated: they change what the model
  is evidence for.

## checkCIF

The IUCr checkCIF service validates a CIF and reports alerts by level.
A-level alerts must be resolved or explained in a response text; B-level
alerts are explained; C and G are informational. Run it before submission and
keep the report. Do not describe a structure as validated because it parsed —
validation is the alert list with its explanations.

## Powder and other cases

Powder XRD gives phase identification against a reference pattern database
and, with Rietveld refinement, lattice parameters and phase fractions.
Report the refinement software, background and peak-shape model, R_wp and
R_exp, and show the difference plot; a Rietveld fit judged only by R_wp is
not judged. Preferred orientation and amorphous content bias phase
quantification — say how they were handled.

## Deposition

Small-molecule structures deposit with the CCDC and receive a CCDC number;
macromolecular structures deposit with the wwPDB and receive a PDB ID.
Deposit before submission, cite the identifier in the paper, and resolve it
with `cite_add` plus `cite_check(resolve=True)` rather than typing an
accession from memory. Where structures come from the CSD, ICSD or COD, check
each database's licence before redistributing coordinates.

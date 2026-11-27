# Characterisation: what each method supports

Match technique to claim. Anything beyond the bounded question a technique
answers needs a second, independent technique.

## Diffraction

**XRD** gives crystalline phases, lattice parameters, crystallite size and
strain (from peak broadening, with the instrumental broadening subtracted),
texture and residual stress with the right geometry. It does not give
morphology, and it is weak on amorphous content and on phases below roughly a
percent. Report radiation and wavelength, geometry, step size, dwell,
sample preparation, the reference patterns matched, and for a Rietveld
refinement the software, the fitted model, R_wp and the difference plot.

## Electron microscopy

- **SEM** — surface morphology and, with backscatter, atomic-number
  contrast. Report accelerating voltage, working distance, detector, and any
  conductive coating. A coating changes the surface you are imaging.
- **EDS** — elemental composition, semi-quantitative. The interaction volume
  is micron-scale, so a "point" analysis on a sub-micron feature includes its
  surroundings. Light elements are unreliable; overlapping lines are
  routinely misassigned. Quote it as semi-quantitative unless standards-based
  and say so.
- **EBSD** — grain orientation, boundary character, texture and phase
  distinction between phases with different symmetry. Report step size,
  indexing rate and clean-up steps; aggressive clean-up manufactures grains.
- **TEM/STEM** — local structure, defects, interfaces, and with EELS light
  elements and bonding. The sample is thinned and may be damaged by the
  preparation; it is a very small volume and is not automatically
  representative.

## Surface and thermal

- **AFM** — topography convolved with the tip shape; report tip, mode and
  scan rate, and beware of tip artefacts on steep features.
- **XPS** — surface chemistry and oxidation state in the top few nanometres.
  Report the charge reference used; binding-energy assignments come from a
  published reference database, not from memory.
- **DSC / TGA** — transitions, heat flow and mass loss at a stated ramp rate,
  atmosphere, pan type and sample mass. All of those change the result;
  compare only runs collected identically.

## Sampling and honesty

One micrograph is an anecdote. State how many specimens, how many regions per
specimen, and how they were chosen — random, systematic, or "representative",
and if representative, by what criterion. Quantitative image analysis
(grain size, porosity, phase fraction) reports the number of fields, the
number of counted features, the thresholding procedure and the dispersion,
and follows a named standard where one exists.

Image processing is data processing: keep the raw images, script the
analysis, and run it through `analysis_run` so the parameters and outputs are
in the ledger. Adjusting contrast on a figure is fine and must be disclosed;
adjusting it on one panel of a comparison is not.

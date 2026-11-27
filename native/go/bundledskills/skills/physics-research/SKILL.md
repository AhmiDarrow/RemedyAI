---
name: physics-research
description: >
  Use when the work is a physical measurement, a fit, an error budget, a
  detector or instrument analysis, or a simulation that has to agree with
  known physics. Covers dimensional checks, CODATA constants, Type A/B
  uncertainty and coverage factors, chi-square and covariance, systematics
  and blind analysis, HDF5/ROOT/FITS data and the arXiv/Zenodo/HEPData
  archives.
version: 1.0.0
author: Remedy
tags: [research, physics, metrology, uncertainty, fitting, simulation]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, stats_assumptions, stats_effect_size, manuscript_build, lit_search, lit_fetch, cite_add, cite_check, skill_activate, file_read, file_write, repo_search]
triggers:
  - '\b(systematic uncertaint\w+|statistical uncertaint\w+|error budget|error propagation|dimensional analysis|units check)\b'
  - '\b(Monte[- ]Carlo (?:event|generator)|GEANT4?|ROOT (?:file|tree|macro)|detector (?:calibration|response|acceptance))\b'
  - '\b(blind(?:ed)? analysis|look[- ]elsewhere effect|five sigma|5\s?sigma|goodness[- ]of[- ]fit|chi[- ]?squared per (?:dof|degree))\b'
  - '\b(hep-(?:ex|ph|th|lat)|cond-mat|astro-ph|gr-qc|quant-ph|nucl-(?:ex|th))\b'
---

# Physics research

Activate `research-method` first: `skill_activate(skill="research-method")`.
That pack owns question framing, evidence standards, preregistration,
citation honesty and how to say "we do not know". Do not restate it. What
follows is only what physics does differently.

## Order of operations

1. **Dimensions before numbers.** Reduce every term of the expression to base
   SI dimensions (M, L, T, I, temperature, N, J). Every summand must match,
   and the result must match the quantity claimed. A dimensional mismatch is
   findable in thirty seconds; a missing 2*pi is not, so also evaluate one
   limiting case whose answer is already known.
2. **Constants from CODATA, never from memory.** Take values and standard
   uncertainties from the NIST Fundamental Physical Constants dataset and
   record which CODATA adjustment year you used. Since the 2019 SI
   redefinition c, h, e, k_B and N_A are exact by definition; G and particle
   masses are measured and carry uncertainties. Never type a constant from
   recall into a result the owner will publish.
3. **Uncertainty model before you fit.** Per input, decide Type A (evaluated
   from repeated observations) or Type B (calibration certificate, datasheet
   tolerance, digitiser resolution, a published value). Write the list down
   before touching the data — see `references/uncertainty-propagation.md`.
4. **Fit, then interrogate the fit** (below).
5. **Systematics last and hardest** — `references/systematics-and-blinding.md`.
6. **Reproduce.** Every number that reaches the paper comes out of
   `analysis_run`, so the ledger holds argv, input hashes and artifact
   hashes. `analysis_ledger(action="verify")` is how you show a figure still
   matches the data it came from.

## Quoting a result

`value ± u` means nothing until the reader is told what the ± is. State the
standard uncertainty u (k=1) or the expanded U with its coverage factor k and
the approximate coverage probability; give statistical and systematic parts
separately; state correlations between quoted quantities. Round the
uncertainty to one or two significant figures first, then round the value to
the same decimal place — no more digits than the uncertainty supports, and no
fewer. Units on every axis, table column and abstract number.

## Fitting

- Least squares is maximum likelihood only for Gaussian errors. Counting data
  wants a Poisson likelihood; do not chi-square bins with a handful of
  entries.
- Report chi-square per degree of freedom **with the dof** and the p-value.
  chi2/dof near 1 says the residuals are consistent with the errors you
  quoted — it does not say the model is right, and chi2/dof well below 1
  usually means the errors are overstated.
- Report the covariance matrix, not only the diagonal. Correlated parameters
  make naive propagation wrong.
- A good fit to a wrong model is ordinary. Plot residuals against every
  independent variable, look for structure, and fit at least one alternative
  model before claiming the first one.

## Blind analysis

Fix selection, calibration and fit procedure while the signal region or final
value is hidden — an unknown additive offset, a scrambled or held-back
subset, a masked region. Unblind once. Everything done after unblinding is
labelled post-unblinding in the write-up. The field does this because it is
the only cheap defence against tuning cuts until the answer looks right.

## Simulation

A simulation is not evidence until it reproduces something already known: an
analytic limit, a conserved quantity (energy, momentum, charge, unitarity,
detailed balance), a published benchmark, or its own coarse-grid answer under
refinement. Show the convergence order you actually observed. Seed every
generator explicitly and record the seed in the run.

## Dual use

You do the ordinary published physics — cross-sections from evaluated nuclear
data libraries, detector response, shielding calculations as taught, reactor
kinetics from textbooks, radiation dosimetry, materials under irradiation.
You decline weapons-relevant specifics: device geometry, yield or criticality
engineering for a weapon, enrichment process detail, and component or
precursor procurement. When a request lands there, say so plainly, point at
the governing process — the institutional radiation safety committee, the
export-control or licensing office — and carry on with the legitimate part of
the work rather than routing around review.

## Verification — what "done" means here

- Dimensions balance and one limiting case reproduces a known result.
- Every uncertainty has a type (A/B), a coverage factor, and stated
  correlations; the systematic budget is a table where each row names the
  method used to estimate it.
- The fit reports chi2/dof with dof, the covariance, and a residual plot.
- The analysis reruns from the ledger with matching input and artifact
  hashes (`analysis_ledger(action="verify", run_id=...)`).
- `cite_check(resolve=True)` returns PASS; unresolved citations are reported
  to the owner, never quietly kept.
- `manuscript_build` reports no undefined citations or references.

Read `references/INDEX.md` and pull what you need with `file_read`.

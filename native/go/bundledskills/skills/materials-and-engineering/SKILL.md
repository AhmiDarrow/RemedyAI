---
name: materials-and-engineering
description: >
  Use for materials and device work: structure-property-processing reasoning,
  characterisation (XRD, SEM/TEM/EDS, AFM, DSC/TGA), mechanical testing and
  Weibull statistics for brittle failure, FEA mesh convergence and material
  models, SPICE and CFD sanity checks, tolerances and GD&T, ASTM/ISO/IEC
  method numbers, datasheets and derating, measurement uncertainty and
  calibration traceability, and open materials data.
version: 1.0.0
author: Remedy
tags: [research, materials, engineering, characterisation, simulation, standards]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_profile, data_diff, stats_assumptions, stats_effect_size, power_analysis, manuscript_build, lit_search, lit_fetch, cite_add, cite_check, skill_activate, file_read, file_write]
triggers:
  - '\b(stress[- ]strain|Young.?s modulus|yield strength|fatigue (?:life|limit|test)|fracture toughness|hardness test)\b'
  - '\b(\bSEM\b imaging|\bTEM\b imaging|\bEDS\b|\bEBSD\b|nanoindentation|tensile test|creep test)\b'
  - '\b(finite element (?:analysis|model|mesh)|\bFEA\b|ASTM [A-Z]?\d{2,4}|ISO \d{3,5})\b'
  - '\b(microstructure|grain (?:size|boundar\w+)|sintering|anneal(?:ing)? (?:step|temperature)|phase diagram)\b'
---

# Materials and engineering

Activate `research-method` first: `skill_activate(skill="research-method")`.
It owns question framing, evidence standards, citation honesty and how to say
"we do not know". Do not restate it. This pack is what materials and device
work does differently.

## The spine: processing - structure - property - performance

Every claim sits somewhere on that chain, and a claim only travels one link
at a time. Processing history (composition, thermal path, deformation,
atmosphere, cooling rate) produces a structure; structure produces
properties; properties, plus geometry and loading, produce performance. A
result reported without its processing history cannot be reproduced, and a
property measured on one geometry does not transfer to another without an
argument. Write the processing history into the methods section first, then
the characterisation, then the property.

## Measure with a named method

Give the standard's number, its issuing body and its year in the methods:
ASTM International, ISO, IEC, ANSI, DIN, JIS, NIST. The number tells a reader
the specimen geometry, conditioning, strain rate, fixture, sample count and
reporting format in one token — which is exactly why it belongs in the paper
and why "a tensile test" does not. Standards are revised: quote the edition
you followed and check the issuing body's catalogue for the current one
rather than trusting a remembered designation. If you deviate from the
standard, say which clause and why.

## Characterisation: match the technique to the claim

Each technique answers a bounded question, and a claim beyond that bound
needs a second technique. XRD gives phases and lattice parameters, not
morphology. SEM gives surface morphology; EDS gives semi-quantitative
composition with poor light-element sensitivity and a micron-scale
interaction volume, not trace analysis. TEM gives local structure at the cost
of a thinned, possibly damaged, and certainly unrepresentative sample. AFM
gives topography convolved with the tip. DSC/TGA give transitions and mass
loss at a stated ramp rate and atmosphere. See
`references/characterisation-methods.md`. Report instrument, settings and how
many regions or specimens; one micrograph is an anecdote.

## Statistics of failure

Strength of a brittle material is a distribution, not a value — the
weakest-link statistics mean the mean alone is misleading and the size of the
specimen matters. Fit Weibull, report the modulus, the characteristic
strength, the confidence intervals and the number of specimens, and state the
estimator. Use `power_analysis` to size the sample before testing and
`stats_assumptions` on the data before choosing a test.
`references/mechanical-testing-and-weibull.md`.

## Simulation is a claim until it is validated

Verification asks whether the equations are solved correctly; validation asks
whether they are the right equations for this system. Both are required, and
they are separate statements. For FEA: mesh convergence with the observed
convergence rate, element type and order justified, boundary conditions and
contact stated, and the material model named with its validity range —
linear-elastic results past yield are not results. For circuits: SPICE model
provenance, corner and Monte-Carlo runs, convergence settings. For CFD: y+,
turbulence model, grid-convergence index. Validate against a measurement or
an analytic case and show the comparison.
`references/fea-and-mesh-convergence.md`,
`references/circuit-and-system-simulation.md`.

## Verification — what "done" means here

- The method names its standard (body, number, year) and every deviation.
- Instruments are calibrated with traceability stated, and the measurement
  uncertainty is reported, not just the reading —
  `references/measurement-uncertainty.md`.
- Sample size is justified before the test, and dispersion is reported with
  every mean (sd or CI, and n).
- Simulations show a convergence study and a validation case; the material
  model is used inside its stated range.
- Tolerances and fits are stated as dimensions with limits, and safety
  factors and derating name the standard or datasheet they came from.
- Every figure traces to a run in the ledger
  (`analysis_ledger(action="verify")`).
- `cite_check(resolve=True)` returns PASS; `manuscript_build` reports no
  undefined citations or references.

Read `references/INDEX.md` and pull what you need with `file_read`.

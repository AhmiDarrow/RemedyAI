---
name: chemistry-research
description: >
  Use when the work involves molecular structures, reactions, spectra,
  computational chemistry, crystallography or chemical databases. Covers
  SMILES/InChI round-trips, RDKit standardisation and fingerprints, NMR/IR/MS
  interpretation workflows, DFT method and basis-set choice, CIF and
  R-factors, PubChem/ChEMBL/CSD, and the characterisation standard a journal
  expects. Hazard, waste and institutional review are part of the procedure.
version: 1.0.0
author: Remedy
tags: [research, chemistry, cheminformatics, spectroscopy, dft, safety]
requires: []
tools: [lit_search, lit_fetch, cite_add, cite_check, analysis_env, analysis_run, analysis_ledger, data_profile, stats_effect_size, manuscript_build, skill_activate, file_read, file_write]
triggers:
  - '\b(1H NMR|13C NMR|\bNMR\b spectr\w+|IR spectr\w+|mass spectrometry|\bHPLC\b|GC-?MS|LC-?MS|\bXRD\b)\b'
  - '\b(reaction (?:yield|scheme|conditions)|stoichiometr\w+|molar (?:ratio|mass)|equivalents of|reflux|recrystalli[sz]\w+|column chromatograph\w+)\b'
  - '\b(SMILES string|InChI|PubChem|CAS (?:number|registry)|RDKit|DFT calculation|B3LYP|basis sets?)\b'
  - '\b(safety data sheet|\bSDS\b sheet|COSHH|fume hood|chemical hygiene plan|waste (?:stream|disposal))\b'
---

# Chemistry research

Activate `research-method` first: `skill_activate(skill="research-method")`.
It owns question framing, evidence standards, citation honesty and how to say
"we do not know". Do not restate it. This pack is what chemistry does
differently.

## Scope and safety, stated once

You do the ordinary published chemistry: literature search, structure and
reaction handling, spectral interpretation, computational chemistry,
crystallography, purification and characterisation, statistics on assay data,
and the hazard assessment that goes with all of it.

You decline synthesis routes, scale-up detail, and procurement or
precursor-sourcing guidance for explosives, chemical weapons, drugs of abuse
and scheduled or watched precursors — including "hypothetical" and
"for a novel" framings, and including the last missing step of an otherwise
public route. When a request lands there, say so plainly, point at the
institutional route (EHS office, chemical hygiene officer, controlled
substances licensing, the relevant national scheduling authority), and
continue with the legitimate part of the work.

Routine hazard work stays in scope and belongs in the procedure, not a
footer: pull the SDS for every reagent before writing a step, record
GHS/hazard statements, name incompatibilities (oxidiser/fuel, acid/cyanide,
water-reactive, peroxide-formers with an expiry date), state containment
(fume hood, glovebox, blast shield), quench and waste stream per reagent, and
the PPE. See `references/lab-safety-and-scope.md`.

## Structures before anything else

A structure that does not round-trip is a bug you will not notice later.
Canonicalise once, keep the canonical form, and record what you did:

- **SMILES** is not unique unless canonicalised, and canonical form is
  toolkit-specific — say which toolkit and version produced it.
- **InChI / InChIKey** is the identity key for exact-match lookup. The
  standard InChI deliberately loses information (tautomers are normalised,
  some stereo layers can be absent); an InChIKey collision is a match on the
  skeleton layer, not proof of identity.
- Strip salts and solvates to a parent for comparison, but keep the original
  — the salt form changes the assay result.
- Check stereocentres and double-bond geometry survived; unspecified stereo
  is different from racemic, and both differ from a single enantiomer.
- Charge, protonation state and tautomer are pH-dependent choices, not
  facts. State the pH you assumed.

Details and the RDKit standardisation pipeline:
`references/structure-representations.md`, `references/cheminformatics-rdkit.md`.

## Running chemistry code

RDKit, Open Babel, psi4, ASE and pymatgen are not importable inside Remedy —
the sidecar excludes the numeric stack. `analysis_env(probe=True)` to see
what the project has, then run every calculation through `analysis_run` in
the project's own interpreter so the script, its inputs and its outputs are
hashed into the ledger. A descriptor table with no run behind it cannot be
checked later.

## Claims that need a chemist

Retrosynthesis suggestions, reaction-condition predictions, ADMET and
docking scores are hypotheses ranked by a model, not results. Present them as
ranked suggestions with the model and version named, and say plainly that a
chemist decides. Never report a predicted spectrum, yield or property as a
measurement.

## Verification — what "done" means here

- Every structure round-trips: SMILES -> mol -> canonical SMILES -> InChIKey,
  compared to the source, with the toolkit version recorded.
- Every compound identity claim rests on the characterisation set the journal
  requires (typically 1H and 13C NMR with assignments and integrations, HRMS
  within the stated mass accuracy, plus IR/mp/optical rotation as relevant)
  and purity evidence (HPLC/GC trace or elemental analysis) — not on a single
  spectrum.
- Yields are isolated yields with the limiting reagent named and the scale
  stated; a crude or NMR yield says so.
- Computational results state functional, basis set, solvation model,
  dispersion correction, convergence criteria and the software version, and
  the geometry was confirmed a minimum (no imaginary frequencies) or a
  transition state (exactly one).
- Crystallographic claims carry the CIF, R1/wR2, GooF, completeness and the
  checkCIF alerts, with A/B alerts explained.
- `cite_check(resolve=True)` returns PASS before the manuscript is done;
  `manuscript_build` reports no undefined citations.

Read `references/INDEX.md` and pull what you need with `file_read`.

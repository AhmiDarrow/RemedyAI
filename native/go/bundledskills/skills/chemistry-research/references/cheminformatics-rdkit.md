# Cheminformatics with RDKit

RDKit cannot be imported inside Remedy. Everything below runs as a script in
the project's environment through `analysis_run`, printing JSON. Probe with
`analysis_env` first and report the RDKit version — descriptors and canonical
SMILES change between them.

## Standardisation pipeline

Run one pipeline over every molecule, in a fixed order, and record it:

1. Parse with sanitisation on; keep parse failures in a rejects file.
2. Remove explicit hydrogens you do not need; keep them where stereo or
   tautomer logic depends on them.
3. Normalise functional groups (nitro, azide, N-oxide drawing variants).
4. Reionise, then choose a protonation state at a stated pH.
5. Strip salts and solvates to the largest organic fragment, keeping the
   original alongside.
6. Canonicalise tautomers with a named algorithm, or skip that step and say
   so — half-normalising is worse than not.
7. Emit canonical isomeric SMILES + InChIKey; dedupe on InChIKey.

Report counts at every step; a pipeline that silently halves the dataset
changes the result.

## Descriptors

MW, logP estimates, TPSA, rotatable bonds, HBD/HBA, ring counts, fraction
sp3. Two cautions:

- Computed logP is a **prediction** from a group-contribution or ML model,
  not a measurement. Name the model; a measured logP needs a citation.
- Descriptor definitions differ between packages, so a TPSA from one toolkit
  is not interchangeable with another's. Build the whole table with one
  toolkit and version.

## Fingerprints and similarity

- Morgan/ECFP (circular, radius r, folded to n bits) is the usual default.
  Radius and bit length are part of the result — ECFP4 means radius 2.
  MACCS keys, atom pairs and torsions answer different questions.
- Tanimoto on ECFP4 is the usual similarity. What it **does not** mean: not
  a probability, not biological similarity, not comparable across
  fingerprint types, radii, bit lengths or datasets. The familiar "0.85
  means similar" cut came from particular datasets and does not transfer.
- Similarity is relative to a reference distribution: report the background
  distribution of pairwise similarity in your library, or it cannot be read.
- Activity cliffs are real — near-identical structures, very different
  activity. High similarity is a hypothesis, not a prediction.

## Splits and models

If a model is trained on this data, activate `ml-research` for leakage,
split and calibration rules. The chemistry-specific part: random splits leak
through analogue series, so use scaffold, cluster or time splits and say
which.

## Reactions and retrosynthesis

Reaction SMILES/SMARTS and RInChI encode transformations; atom mapping is
needed for mechanism and is often wrong in bulk datasets.
Retrosynthesis and condition-prediction tools emit ranked suggestions from a
model trained on published reactions — keep them labelled as suggestions,
name the model and version, and say a chemist decides.

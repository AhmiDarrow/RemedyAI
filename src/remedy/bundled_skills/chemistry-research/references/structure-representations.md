# Structure representations and round-trips

## The formats

- **SMILES** — compact line notation. Not unique unless canonicalised, and
  canonical output differs between toolkits and versions. Record which
  toolkit and version produced a canonical SMILES. Isomeric SMILES carries
  `@`/`@@` and `/`, `\`; plain SMILES silently drops them.
- **InChI** — layered, algorithmic, deterministic. Layers: formula,
  connections, hydrogens, charge, stereo, isotope. Standard InChI normalises
  mobile hydrogens (tautomers) and may omit stereo layers, so two genuinely
  different tautomers can share one standard InChI. Non-standard InChI keeps
  more but is not comparable across sources.
- **InChIKey** — 27-character hash of the InChI. First block is the skeleton,
  second the remaining layers, last the protonation flag. Equal first blocks
  mean same connectivity, not same compound. It is a lookup key, not proof.
- **MOL / SDF** — atom and bond blocks with coordinates; SDF adds tagged data
  fields. V2000 caps at 999 atoms/bonds; use V3000 above that. Wedge/hash
  bonds encode stereo only when coordinates agree with them.
- **CML** — XML; verbose, keeps arbitrary metadata cleanly.
- **CAS Registry Number** — assigned by CAS, not derivable from structure.
  Never guess one; look it up and cite the source.

## Round-trip discipline

Do this once, at ingest, and store the result:

    input -> parse -> sanitise -> canonical isomeric SMILES -> InChI -> InChIKey

Then re-parse the canonical SMILES and confirm the InChIKey is unchanged. A
mismatch means information was lost or the input was ambiguous. Log failures
rather than dropping them silently; a "cleaned" dataset that quietly lost
part of its stereochemistry produces a result nobody can reproduce.

## Where round-trips break

- **Stereochemistry**: unspecified is not racemic, and racemic is not a
  single enantiomer. Relative vs absolute configuration must be distinguished.
  Atropisomers and stereogenic axes are often lost.
- **Tautomers**: keto/enol, amide/imidic, azole ring tautomers. Pick one
  convention, apply it to the whole dataset, and say which. Do not compare a
  tautomer-normalised set against a raw one.
- **Salts, solvates, hydrates**: strip to a parent for comparison, keep the
  original for anything measured — solubility, melting point and assay values
  belong to the actual solid form.
- **Charge and protonation**: pH-dependent. State the pH assumed; a
  zwitterion drawn neutral is a different molecule to a model.
- **Aromaticity models** differ between toolkits; kekulised and aromatic
  forms of one ring may not compare equal across libraries.
- **Isotopes and radicals** are easily dropped in conversion; check them
  explicitly if the work depends on them.

## Doing it here

Parsing needs RDKit or Open Babel, which Remedy cannot import. Run the
round-trip as a script in the project environment through `analysis_run`,
emit a JSON report of successes and failures, and keep it in the ledger.

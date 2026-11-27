# Reagents: validation, identifiers and lots

## Identify everything with an RRID

RRIDs (Research Resource Identifiers) exist for antibodies, cell lines,
organisms/strains, plasmids and software. Look the identifier up in the
resource registry rather than reconstructing one from memory, and record it
beside the vendor and catalogue number. Journals increasingly require them,
and they are the only way a reader can tell which of forty similarly named
antibodies you used.

Minimum row per reagent: name, vendor, catalogue, lot, RRID, dilution or
concentration, and where it was validated.

## Antibodies

Catalogue number alone is not validation. State which of these you have:

- **Genetic** — signal lost in a knockout/knockdown of the target.
- **Orthogonal** — signal tracks an independent abundance measure (mRNA, MS).
- **Independent antibody** — a second clone to a different epitope agrees.
- **Recombinant expression** — signal appears when the target is expressed.
- **Immunocapture-MS** — the pulled-down protein is identified.

Validation is application-specific: validated for western blot is not
validated for IF or IHC. Record the application and species. Lot changes
drift — record the lot and revalidate.

## Cell lines

- **Authenticate by STR profiling** (SNP panels for non-human lines) on
  receipt, before banking, and at publication. Record the date and database.
- **Check the ICLAC register of misidentified cell lines** before starting —
  look it up; do not rely on recall for whether a line is contaminated.
- **Mycoplasma test** roughly monthly and before any dataset to be
  published; record date and method.
- Record passage number per experiment, within a stated window. Freeze a
  low-passage master bank and work from working banks.
- Record medium, serum lot and supplements.

## Plasmids, strains and edits

- Sequence-verify the insert **and** its junctions (full-plasmid sequencing
  is cheap enough to be the default). Record backbone, resistance, promoter
  and tag position, and cite the repository accession it came from.
- CRISPR edits need more than a band on a gel: sequencing across the edit
  site with allele deconvolution, plus protein-level loss for a knockout.
  Report the gRNA, the predicted off-targets you checked, and whether a
  second independent gRNA reproduces the phenotype.
- siRNA/shRNA: two independent sequences, a non-targeting control, and
  knockdown measured at the protein level, not only mRNA.

## Small molecules and biologics

Record supplier, catalogue, lot, purity/certificate of analysis, solvent,
stock concentration, storage temperature, freeze-thaw count and the final
vehicle percentage. Degradation and repeated freeze-thaw explain a surprising
number of "the effect went away" results.

## Where this lands in the paper

A reagents table in the methods or supplement, machine-readable.
`manuscript_check` with the MDAR base layer flags missing columns; fill them
from the notebook, not from memory.

# Chemical databases and licensing

## What each one is for

- **PubChem** (NCBI) — compounds, substances and bioassays, open, with a REST
  interface (PUG-REST) and identifier conversion. Note the CID/SID
  distinction: a CID is a normalised compound, an SID a depositor record.
  Data quality varies by depositor; a PubChem value needs its depositor
  named.
- **ChEMBL** (EMBL-EBI) — bioactivity extracted from the medicinal-chemistry
  literature, with assay descriptions and confidence scores. Filter on assay
  type and confidence before aggregating; mixing IC50 from different assay
  formats produces a meaningless distribution.
- **CSD** (Cambridge Crystallographic Data Centre) — small-molecule crystal
  structures. Subscription, with defined terms for what may be redistributed.
- **ICSD** — inorganic crystal structures; **COD** is the open alternative.
- **Reaxys** (Elsevier) and **CAS SciFinder / CAS Registry** — reaction and
  substance databases behind institutional subscriptions. Their content and
  identifiers are licensed; bulk extraction usually breaches the agreement.
- **NIST Chemistry WebBook** — thermochemistry, spectra, ion energetics,
  with the original references attached.
- **DrugBank**, **BindingDB**, **ZINC**, **Protein Data Bank**, **MassBank**,
  **GNPS**, **nmrshiftdb2** — targeted resources, each with its own terms.

## Rules for using them

1. Record the database, the accession, the version or release date, and the
   retrieval date with every value. `lit_search` and `cite_add` capture
   `source` and `retrieved_utc` for you; keep them.
2. A value in a database is a claim by whoever deposited it. Follow it to the
   primary reference before it becomes a number in the owner's paper. If the
   primary reference cannot be found, report that instead of citing the
   database as if it were the measurement.
3. Never invent a CAS number, CID, ChEMBL id, CCDC number or accession.
   Absent means absent, and the payload says where to look.
4. Check the licence before redistributing. Open (PubChem, COD, PDB,
   MassBank) is not the same as free-to-mirror in every case, and
   subscription content (CAS, Reaxys, CSD) generally cannot be redistributed
   at all. When the owner wants a derived dataset published, the licence
   question is part of the work, not an afterthought.
5. Rate limits are real. Batch queries, respect the documented limits, and do
   not retry-loop on a 429 — degrade to another source and say so in the
   notes.

## Verifying

`cite_check(resolve=True)` is the only step allowed to call a citation
verified, and only for identifiers it resolved in that run. For a database
accession with no DOI, the honest record is the accession plus the retrieval
date plus the exact query, so the owner can re-run it.

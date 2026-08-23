# Data formats and archives

## The formats you will meet

- **HDF5** (`.h5`, `.hdf5`) — hierarchical groups and datasets with
  attributes. Self-describing: read the attribute dictionary before guessing
  units. Chunking and compression affect read speed, not content. Tools:
  `h5ls`, `h5dump`, h5py in the project env.
- **ROOT** (`.root`) — trees of branches, common in particle and nuclear
  physics. Read with the project's ROOT or uproot (pure Python, usually
  easier). A TTree is columnar — read the branches you need.
- **FITS** (`.fits`) — astronomy standard: header cards plus image or table
  HDUs. The WCS keywords define the coordinate mapping; never re-derive a
  projection you can read from the header. Tools: astropy.io.fits,
  `fitsheader`.
- **NetCDF / CF** — gridded data with CF conventions for units and
  coordinates; earth-adjacent physics uses it heavily.
- **Plain text / CSV** — fine for small tables. Record column units in a
  header comment; `data_profile` reads it with the stdlib engine.

Remedy's own process has no h5py, uproot, astropy or ROOT and must not import
one. Read these files with a short script run in the project environment
through `analysis_run` that prints JSON. Check with `analysis_env(probe=True)`
first; if the library is missing, name what to install rather than guessing.

## Metadata that must travel with the data

Units, coordinate system and epoch, calibration version, run/observation id,
timestamp with timezone, instrument configuration, and the software version
that wrote the file. A dataset without these cannot be reanalysed.

## Where the literature and data live

- **arXiv** — preprints by category (hep-ex, hep-ph, hep-th, hep-lat,
  cond-mat, astro-ph, gr-qc, quant-ph, nucl-ex, nucl-th). `lit_search
  (source="arxiv")`. An arXiv id is not a DOI; a published record usually has
  both — record both.
- **Zenodo** — mints DOIs for datasets, software and figures; the usual place
  to deposit analysis code and the data behind a figure. Concept DOI vs
  version DOI: cite the version you used.
- **HEPData** — the archive for particle-physics measurements: tables,
  covariances, likelihoods. If the paper you are reproducing has a HEPData
  record, take the numbers from there instead of digitising a plot.
- **CDS** (CERN Document Server) — CERN preprints, notes and internal
  documents.
- **NASA ADS** — astronomy bibliography; **MAST**, **ESA archives**, **IRSA**
  for the observations themselves.

Verification: resolve the identifier. `cite_add` the record, then
`cite_check(resolve=True)` — the only step in this repo allowed to call a
citation verified, and only for identifiers it resolved in that call. If a
DOI does not resolve, report it to the owner.

## Depositing

The reproducibility package is: analysis code at a tagged commit, the input
data or its accession, the `analysis_env` description, and the ledger entries
for the runs behind each figure. Deposit, take the DOI, cite it.

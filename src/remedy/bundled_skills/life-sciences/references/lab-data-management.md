# Lab notebooks, sample tracking and data provenance

## The chain that must never break

    animal / donor / vial  ->  sample id  ->  plate well  ->  raw file
      ->  processed table  ->  figure panel

Every arrow needs a file that records it. When a reviewer asks "which mouse
is that point?", the answer must be reachable without anyone's memory. Keep
join keys as plain columns, not as information encoded in a file name that a
rename destroys.

## Identifiers

- One immutable sample id per biological unit, assigned at collection, never
  reused, never derived from the treatment. `M2026-03-014`, not `treated_3`.
- Do not put the arm in the id if the outcome will be scored blind.
- Avoid ids spreadsheets mangle: leading zeros, anything that parses as a
  date (`SEPT2`, `MARCH1`, `1-2`), bare numbers. Prefix with letters.
- A plate map file (well -> sample id, arm, concentration, batch) written
  before the run and saved beside the instrument export. Exports give well
  positions; without the map they are anonymous.

## Notebook entries that are worth having

Per experiment: date, operator, aim, protocol version and every deviation,
reagent lots, instrument and settings, the randomisation seed or allocation
file, raw file names, and what went wrong. The deviations and the failures
are the part that saves the next month.

Electronic notebooks (Benchling, eLabFTW, LabArchives, or a dated markdown
file in the repo) all work. What matters: entries are dated, append-only in
practice, and reference the raw files by path.

## Files

- **Raw is immutable.** Keep vendor formats untouched in a `raw/` tree,
  read-only, backed up in two places, and never edit them in place. All
  processing writes to `processed/` or `results/`.
- Name files so sorting is chronological and the id is present:
  `2026-03-14_plate02_A549_dose_raw.csv`.
- Excel corrupts data silently — autocorrected gene names, truncated
  precision, hidden rows. Export CSV as the analysis input and `data_profile`
  it to catch the damage (mixed types, whitespace, dates where numbers go).
- Checksum every raw file you analyse. `analysis_run` records input hashes;
  `analysis_ledger(action="verify")` later says whether the figure still
  matches the data it came from.

## Metadata that travels with a sample

Species/line and authentication date, sex, age or passage, genotype,
treatment and dose, timing, collection method, storage and freeze-thaw count,
operator, batch. For human material also: consent scope, de-identification
method, and the approval it was collected under — that constrains what may be
done with it later. Never put identifiable human data in a shared analysis
directory.

## Retention and handover

Decide at the start where the data lives afterwards: an institutional
repository, a domain repository, or a controlled-access archive. Register an
accession and cite it with `cite_add` so the manuscript points at a real
deposit rather than "available on request".

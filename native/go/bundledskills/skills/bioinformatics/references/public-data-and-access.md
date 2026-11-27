# Public data, access tiers and compute scale

## Where things live

| Resource | Holds |
|---|---|
| SRA (NCBI) / ENA (EBI) / DDBJ | raw reads; mirrored between them |
| GEO / ArrayExpress-BioStudies | processed series with sample metadata |
| Ensembl, UCSC, RefSeq | genomes, annotation, browser tracks |
| dbGaP, EGA, JGA | controlled-access human data |
| Zenodo, Figshare, OSF | anything else, with a DOI |

Accession shapes: SRA `SRP`/`SRX`/`SRR` (study/experiment/run), ENA `PRJEB`,
NCBI BioProject `PRJNA`, BioSample `SAMN`, GEO `GSE` (series) and `GSM`
(sample). One GEO series maps to a BioProject and to many runs — resolve that
mapping explicitly rather than assuming one file per sample.

## Getting the data

- ENA serves gzipped FASTQ over HTTPS/FTP with a downloadable report table;
  usually the least painful route.
- SRA needs `prefetch` + `fasterq-dump`, and scratch space several times the
  final FASTQ size.
- Verify checksums against the accession's report, and record accession,
  download date and checksum in the run.

## Metadata is often wrong

Public sample metadata is hand-entered free text. Expect swapped sex labels,
missing batch and collection dates, inconsistent tissue terms, and condition
labels that do not match the paper. Reconcile against the paper's own tables
first, check inferred sex against XIST/Y expression where you can, and report
every discrepancy you resolved. `data_profile` catches the structural half.

## Controlled access

dbGaP and EGA data require an approved application, a named data use
agreement, and specified security controls. That means:

- Do not download controlled data onto a laptop, a personal cloud bucket, or
  any machine outside the approved environment.
- Do not paste controlled data — including genotypes, identifiers or sample
  metadata — into a prompt, an issue tracker, or a public repository.
- Consent scope binds the analysis: some datasets are restricted to a disease
  area or forbid certain uses. Read the DUA rather than assuming.
- Aggregate results have their own rules; some agreements limit what summary
  statistics may be released.

When the owner has not got approval, the useful help is preparing the
application, not finding a way around it.

## Depositing your own data

Deposit raw reads (SRA/ENA) and processed matrices (GEO), with a sample sheet
that matches the manuscript's tables exactly. Human data that cannot be
public goes to a controlled-access archive — "available on request" is not a
data availability statement. Register the accession, then `cite_add` it so
the manuscript points at the real deposit.

## Compute scale — when to stop using a laptop

Rough orders of magnitude, worth checking against each tool's docs: a human
WGS BAM is tens of gigabytes per sample; a STAR human index wants tens of
gigabytes of RAM to build and load, while `bwa` needs far less; salmon and
kallisto are comfortable on a workstation. Alignment and assembly break a
laptop first, usually on RAM or scratch space.

Move to the cluster when one sample no longer fits in memory, when a run
exceeds a few hours, or when samples times per-sample time exceeds a working
day. Snakemake and Nextflow both submit to SLURM through a profile, so the
pipeline does not change — only where it runs.

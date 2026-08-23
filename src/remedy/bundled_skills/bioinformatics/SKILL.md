---
name: bioinformatics
description: >
  Use for sequence and omics work: FASTQ/BAM/VCF/GTF/BED handling and their
  coordinate conventions, reference builds and liftover, read QC and trimming,
  alignment and variant calling, RNA-seq and single-cell analysis, Snakemake
  and Nextflow pipelines, and pulling data from SRA/ENA/GEO under the right
  access tier. Reach for it whenever the task touches genomic files, aligners,
  differential expression, or a workflow that must rerun the same way twice.
version: 1.0.0
author: Remedy
tags: [research, bioinformatics, genomics, rna-seq, pipelines, reproducibility]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_profile, data_diff, stats_multiplicity, lit_search, cite_add, skill_activate, bash_exec]
triggers:
  - '\b(FASTQ|FASTA|\bBAM\b|SAM file|\bVCF\b|GTF file|BED file|CRAM file)\b'
  - '\b(RNA-?seq|scRNA-?seq|ChIP-?seq|ATAC-?seq|\bWGS\b|\bWES\b|variant calling|read (?:alignment|mapping))\b'
  - '\b(bowtie2|BWA-?MEM|STAR aligner|salmon (?:quant|index)|kallisto|samtools|bcftools|\bGATK\b|Snakefile|nextflow)\b'
  - '\b(differential expression|DESeq2|edgeR|limma|GO enrichment|KEGG pathway|Ensembl|RefSeq|batch effects?)\b'
---

# Bioinformatics (the dry lab)

Call `skill_activate(skill="research-method")` first — it owns question
framing, evidence standards, preregistration, citation honesty and how to say
"we do not know". Do not restate it. This pack is what is different about
genomic data.

## Start here, in this order

1. `analysis_env(path)` — which of samtools, bcftools, bwa, STAR, salmon, R,
   snakemake, nextflow, conda/mamba, and a project python with pandas
   actually exist here. Nothing below runs in the sidecar; every real step
   goes through `analysis_run` or `bash_exec` in the project environment.
2. **Establish the reference build before touching a file.** GRCh37/hg19,
   GRCh38/hg38 or T2T-CHM13; Ensembl (`1`, `MT`) or UCSC (`chr1`, `chrM`)
   naming; the exact annotation release. Coordinates are meaningless without
   it, and a build mismatch produces plausible, wrong answers rather than an
   error. See `references/reference-genomes.md`.
3. **Check coordinate conventions.** BED is 0-based half-open; GFF/GTF, SAM,
   VCF and most genome browsers are 1-based inclusive. An off-by-one that
   shifts every feature by one base is the commonest silent bug in this
   field. `references/file-formats-and-coordinates.md`.
4. **QC before anything else** — FastQC/MultiQC on the raws, adapter and
   quality trimming, duplicate rate, contamination. A QC gate you skipped is
   a result you cannot defend. `references/qc-and-preprocessing.md`.
5. **Write it as a pipeline.** Snakemake or Nextflow, with pinned tool
   versions in a conda env or container. The pipeline, not the notebook, is
   the reproducibility unit. `references/pipelines-snakemake-nextflow.md`.

## Hard rules

- **Pin everything**: reference FASTA and its checksum, annotation release,
  every tool version, every seed. Record them in the run, not in your head —
  `analysis_run` puts input hashes and argv into the ledger for you.
- **Never mix builds or naming conventions** in one analysis. If you must
  cross them, liftover explicitly, keep the unmapped list, and report how
  many features were lost.
- **Thousands of tests, always.** Every gene, peak, window or variant tested
  needs `stats_multiplicity` (BH FDR is the field default; state the family).
  A gene "significant at p < 0.05" out of 20,000 is not a finding.
- **Counts, not TPM, for differential expression.** DESeq2 and edgeR model
  raw counts with their own normalisation; feeding them TPM or FPKM breaks
  the model. `references/rna-seq.md`.
- **Batch and covariates go into the design formula**, and confounded batches
  cannot be rescued by any method — say so instead of adjusting anyway.
- **In single cell, clustering then testing the genes that defined the
  clusters is circular.** For between-condition claims, pseudobulk per
  sample and test at the sample level. `references/single-cell.md`.
- **Respect the access tier.** Open (SRA/ENA/GEO) and controlled (dbGaP, EGA)
  are different worlds. Controlled data does not go on a laptop, into a
  personal cloud bucket, or into a prompt. `references/public-data-and-access.md`.

## Where the method stops

Do the ordinary published analysis: assembly, alignment, variant calling,
expression, phylogenetics, metagenomics, surveillance data, sequence
annotation of public genomes. Do not provide design or assembly routes for a
pathogen or its genome, edits that would increase transmissibility,
virulence, host range or immune escape, routes to obtain restricted
sequences, or help evading synthesis screening or export control. Say it once
as part of the method and point at the process that governs it — the
institutional biosafety committee, the funder's dual-use policy, the export
control office — then carry on with the science.

## What "verified" means here

A step is verified when the file passes its own integrity check
(`samtools quickcheck`, `bcftools stats`, index present, checksum matches the
manifest), the QC report shows the metric in range, and the whole pipeline
reruns to the same outputs from the same inputs — `snakemake -n` clean,
`nextflow run -resume` producing no changed hashes, and
`analysis_ledger(action="verify")` reporting INTACT. "It ran without an
error" is not verification: an aligner given the wrong reference exits 0.

Read `references/INDEX.md`, then `file_read` what the task needs.

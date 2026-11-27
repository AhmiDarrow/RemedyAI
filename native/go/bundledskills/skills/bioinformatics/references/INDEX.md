# bioinformatics references

- file-formats-and-coordinates.md — FASTA/FASTQ, quality encodings, SAM/BAM/CRAM flags and tags, VCF/BCF, GFF/GTF, BED; the 0-based vs 1-based table and the tools that check each file.
- reference-genomes.md — GRCh37/38/T2T, Ensembl vs UCSC naming, primary vs full assembly, decoys and alt contigs, annotation releases, liftover and what it loses.
- qc-and-preprocessing.md — FastQC/MultiQC metrics and what each failure means, adapter and quality trimming, duplicates, contamination screens, UMIs, when to stop and resequence.
- alignment-and-variants.md — choosing an aligner, read groups, sorting and indexing, duplicate marking, BQSR, HaplotypeCaller/DeepVariant/bcftools, filtering, benchmarking against a truth set.
- rna-seq.md — quantification with salmon/kallisto/STAR, counts vs TPM, DESeq2/edgeR/limma-voom, design formulas, shrinkage, FDR across genes, enrichment done honestly.
- single-cell.md — doublets, ambient RNA, filtering thresholds, normalisation, batch integration, the clustering-then-testing circularity, pseudobulk for condition claims.
- pipelines-snakemake-nextflow.md — why the pipeline is the reproducibility unit, Snakemake and Nextflow shapes, environments and containers, resume semantics, cluster execution.
- public-data-and-access.md — SRA/ENA/GEO retrieval, accession types, metadata that is usually wrong, dbGaP/EGA controlled access, data use agreements, depositing your own data.

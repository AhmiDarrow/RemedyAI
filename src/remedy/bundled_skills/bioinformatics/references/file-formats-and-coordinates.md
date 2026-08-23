# File formats and coordinate systems

## The coordinate table — check this before writing any interval code

| Format | Start | Interval |
|---|---|---|
| BED, bedGraph, bigBed, bigWig | 0-based | half-open `[start, end)` |
| GFF3, GTF | 1-based | closed `[start, end]` |
| SAM/BAM POS, VCF POS, wig | 1-based | closed |
| Browsers (UCSC, Ensembl) as displayed | 1-based | closed |
| htslib/pysam interval APIs | 0-based | half-open |

A BED feature `chr1 100 200` is the 1-based interval `chr1:101-200`, 100 bases
long. Adding one to the start is right; adding one to both ends shifts a whole
annotation. `pysam` returns 0-based positions even from a 1-based file —
convert at the boundary and put the convention in the variable name
(`start0`, `pos1`).

## FASTA / FASTQ

- FASTQ record is four lines; the sequence line may be wrapped in FASTA but
  never in FASTQ. `@` also occurs as a quality character, so never parse by
  looking for `@` at line start.
- Quality encoding: Sanger / Illumina 1.8+ is Phred+33; Illumina 1.3-1.7 was
  Phred+64. FastQC reports which it inferred — check it on old public data
  before trusting any quality-based filter.
- R1 and R2 must hold the same names in the same order; files that fell out
  of sync produce a plausible, wrong alignment.
- `.gz` is fine, but use `bgzip` where a tool wants random access, and keep
  the `.fai` (`samtools faidx`) beside every reference FASTA.

## SAM / BAM / CRAM

- CRAM is reference-compressed: **without the exact reference FASTA it cannot
  be decoded**. Record the reference and its md5 alongside any CRAM.
- Check: `samtools quickcheck -v file.bam` (truncation), `samtools flagstat`,
  `samtools idxstats`, and a `.bai`/`.csi` newer than the BAM.
- Sort order matters: coordinate-sorted for indexing and most callers,
  name-sorted for `fixmate` and paired-read tools. `@HD SO:` says which.
- Read groups (`@RG` with `ID`, `SM`, `LB`, `PL`) are not optional; GATK
  refuses without them, and `SM` becomes the VCF sample name.
- FLAG bits encode paired/proper-pair/unmapped/reverse/secondary/
  supplementary/duplicate. `-F 0x900` is what people usually mean by
  "primary alignments only".
- MAPQ scales differ between aligners — 255 means "unavailable" in some and
  "unique" in STAR. Check the docs before thresholding.

## VCF / BCF

- 1-based, and indels are left-aligned with an anchor base by convention —
  normalise with `bcftools norm -f ref.fa -m -any` before comparing callsets,
  or identical variants will look different.
- Multi-allelic sites, `GT` phasing (`|` vs `/`), and missing (`./.`) all
  change downstream counts; decide and document how each is handled.
- `bcftools stats` and `bcftools query -f` are the tools; do not regex a VCF.

## GFF/GTF and BED

GTF is GFF2 with required `gene_id`/`transcript_id` attributes; feature types
and attribute spellings differ between Ensembl, GENCODE and RefSeq files.
Match the annotation to the reference build and say which release you used.

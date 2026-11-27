# Alignment and variant calling

## Choosing an aligner

| Data | Usual choice |
|---|---|
| Short DNA reads (WGS, WES, amplicon) | `bwa mem` / `bwa-mem2`, `bowtie2` |
| Spliced short RNA reads | `STAR`, `HISAT2` |
| Long reads (ONT, PacBio) | `minimap2` with the right preset |
| Bisulfite | `Bismark`, `BWA-meth` |
| Metagenomes | `bowtie2` to a reference, or a classifier |

Give the aligner the read group inline (`bwa mem -R '@RG\tID:...\tSM:...'`)
so you never have to add it afterwards. Pipe straight into `samtools sort`
and index; do not write an unsorted intermediate SAM to disk.

## The short-read DNA outline

1. Align, sort, index.
2. `samtools markdup` or Picard `MarkDuplicates` — mark, do not remove, so
   the decision stays reversible. Skip for amplicon/UMI libraries.
3. Base quality recalibration (GATK `BaseRecalibrator` + `ApplyBQSR`) where
   the pipeline uses it; it needs a known-sites VCF for the same build.
4. Call: `gatk HaplotypeCaller` (`-ERC GVCF` per sample, then
   `GenomicsDBImport` + `GenotypeGVCFs` for a cohort), `DeepVariant`, or
   `bcftools mpileup | bcftools call`. Somatic calling is a different
   problem: `Mutect2`, `Strelka2`, a matched normal, a panel of normals.
5. Filter: VQSR needs a large cohort; hard filters are the documented
   alternative for small ones. State which you used and the exact thresholds.
6. Normalise (`bcftools norm -f ref.fa -m -any`) before any comparison.
7. Annotate with VEP, SnpEff or ANNOVAR — record the tool version *and* the
   annotation database version; consequence calls change between releases.

## Structural and copy number

SVs need their own callers (`Manta`, `Delly`, `smoove`; `Sniffles` for long
reads) and are far less reliable than SNVs — report the caller, the support
threshold, and validate anything load-bearing by a second method.

## Judging a callset

- `bcftools stats` gives counts, Ti/Tv and indel ratios. Ti/Tv far from the
  expected range signals false positives; look the expected range up for the
  assay rather than recalling a number.
- Depth and allele-balance at called sites: heterozygotes clustered away
  from 0.5 mean trouble.
- Concordance against a **truth set** is the real check. Benchmark samples
  with published high-confidence calls and confident regions exist for this;
  `hap.py` and `rtg vcfeval` do the stratified comparison. Report precision
  and recall inside the confident regions, per variant type.
- Never report a variant count without the filters and regions that produced
  it; "4.3M variants" alone is meaningless.

## Traps

- The wrong reference build gives a valid BAM with a garbage result and exit
  code 0. Check contig lists before and after.
- Removing duplicates on amplicon data destroys the signal.
- Filtering on MAPQ without knowing the aligner's scale (STAR's 255) silently
  drops everything or nothing.
- Comparing two VCFs without normalising counts the same indel twice.
- Human variant data is frequently identifiable; even a small SNP set can
  re-identify a participant. Handle under the study's data use agreement.

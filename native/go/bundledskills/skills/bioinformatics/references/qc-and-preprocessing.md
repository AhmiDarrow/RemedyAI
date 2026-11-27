# QC and preprocessing

Run QC first, look at it, and decide. A pipeline that runs FastQC and never
reads the report has an expensive decoration, not a gate.

## Read-level QC

`fastqc` per file, then `multiqc .` to put every sample on one axis — the
comparison across samples is the part that matters. What each signal means:

- **Per-base quality falling at the 3' end** — normal; trim rather than
  discard. A sharp cliff or mid-read collapse suggests a run problem.
- **Adapter content rising at the 3' end** — insert shorter than the read.
  Expect it in small-RNA and degraded samples.
- **Overrepresented sequences** — adapters, rRNA, polyA, or a genuinely
  highly expressed transcript. Blast one before assuming contamination.
- **Per-sequence GC with a second peak** — contamination or a mixed sample.
  Screen with a tool like FastQ Screen or a k-mer classifier.
- **High duplication** — expected in amplicon and deep RNA-seq; alarming in
  WGS. Look at library complexity, not the raw percentage.
- **Sample swaps** are common and invisible in FastQC. Check genotype
  concordance (`somalier`, `NGSCheckMate`, or a panel of common SNPs)
  whenever the design has paired samples or known relatives.

## Trimming

Tools: `fastp` (QC and trimming in one, writes JSON), `cutadapt`,
`Trim Galore`, `trimmomatic`. Rules:

- Trim adapters always; trim on quality lightly. Aggressive quality trimming
  biases coverage and is unnecessary for soft-clipping aligners.
- Do not quality-trim before variant calling unless the pipeline says to;
  GATK-style pipelines expect untrimmed, soft-clipped reads.
- Enforce a minimum length after trimming, and keep pairs together.
- Record the trimming report per sample; the fraction of reads surviving is a
  QC metric in its own right.

## Post-alignment QC

- `samtools flagstat` / `idxstats`: mapping rate, proper pairs, duplicates.
- Coverage: `mosdepth` or `samtools depth` — mean depth, fraction of target
  above 10x/20x/30x, and uniformity, not just the mean.
- Insert size distribution: a bimodal or very wide distribution means library
  or alignment trouble.
- RNA-seq specifics: rRNA fraction, exonic/intronic/intergenic split, 5'-3'
  coverage bias (degradation), strandedness inferred from the data rather
  than assumed. Getting strandedness wrong halves the counts.
- Feed all of it to MultiQC and keep the report as an artifact of the run.

## Deciding to stop

Set thresholds before looking, write them into the pipeline as an explicit
check, and when a sample fails, exclude it *with the reason recorded* — not
silently. If a large fraction fails, the honest output is "this library needs
resequencing", said early, rather than a rescued analysis whose caveats
nobody reads.

## UMIs

Extract UMIs before alignment (`umi_tools extract`, `fgbio`) and deduplicate
by UMI plus position afterwards. Deduplicating by position alone throws away
real molecules; ignoring UMIs keeps PCR duplicates as if they were evidence.

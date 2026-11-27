# Reference genomes, builds and liftover

## Name the build, always

A human coordinate without a build is not information. Record in the run and
the methods: assembly (GRCh37/hg19, GRCh38/hg38, T2T-CHM13), the exact FASTA
and its checksum, the naming style, and the annotation release (Ensembl,
GENCODE, RefSeq — with its number). Different releases of the *same* build
change gene models and therefore counts.

## Naming styles

- Ensembl: `1`, `X`, `MT`. UCSC: `chr1`, `chrX`, `chrM`.
- Mixing them gives "no reads mapped" or, worse, silently empty `bedtools`
  intersections. Convert with a chromosome mapping file, not a regex that
  also mangles scaffold names.
- hg19's chrM and GRCh37's MT are not the same sequence. Verify before doing
  anything mitochondrial rather than assuming they interchange.
- Scaffold and patch names differ between sources; compare the full contig
  list (`.fai`, `samtools idxstats`, or the VCF `##contig` header) between
  two files before combining them.

## Which flavour of the FASTA

- **Primary assembly** (chromosomes plus unplaced scaffolds, no alt contigs)
  is the safe default for short-read work: alt contigs steal reads and
  depress MAPQ unless the aligner is alt-aware.
- **Analysis sets** for variant-calling pipelines add decoy sequence and
  handle PAR/alt explicitly; if the project follows a published pipeline,
  use the exact reference that pipeline specifies.
- Never index one FASTA and call variants against another. Store the
  reference, its `.fai`, `.dict` and aligner index together and hash them
  into the run record.

## GRCh38 and T2T

GRCh38 fixed many GRCh37 errors and added alt loci; most current tooling and
public resources are built on it. T2T-CHM13 is gapless and resolves
centromeric and acrocentric regions, but it is a single haplotype and much
annotation and population tooling still lags. Choose the build the downstream
resources support, and say why.

## Liftover

Liftover is lossy remapping, not translation.

- Tools: UCSC `liftOver` with the matching chain, `CrossMap` (BED, BAM, VCF,
  wig), Picard `LiftoverVcf` (needs the target FASTA, writes a reject file).
- **Always keep and report the rejected records.** "3.1% of variants did not
  lift" belongs in the results, not in a discarded temp file.
- Strand can flip and REF/ALT can swap between builds; `LiftoverVcf` flags
  this. A liftover that never reports a swap probably did not check.
- Lifted coordinates are approximate in regions restructured between builds.
  Where the claim depends on exact position (a splice site, a primer, a
  clinically reported variant), re-derive it on the target build.
- Never lift twice in a chain (37 -> 38 -> T2T); go back to the source.

## Verifying you have the right one

Take a few positions whose reference allele you know and check them with
`samtools faidx ref.fa chr7:117559590-117559600`. A build mismatch shows up
at once as the wrong base; unchecked, it shows up as an analysis that has to
be redone.

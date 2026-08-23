# Bulk RNA-seq

## Quantification

Two routes, both fine, not interchangeable mid-project:

- **Alignment-free**: `salmon quant` or `kallisto` against a transcriptome
  index built from the same annotation release, then `tximport` into
  gene-level counts (with `countsFromAbundance` chosen deliberately). Fast,
  and handles multi-mapping across isoforms properly.
- **Align then count**: `STAR` to the genome, then `featureCounts` or
  `htseq-count` with the matching GTF. Needed when you also want coverage
  tracks, splicing or variant work from the same BAM.

Set strandedness from the data (`salmon -l A` reports what it inferred;
`infer_experiment.py` or a small `featureCounts` test does the same). A wrong
strand setting typically halves the counts and looks like a failed library.

## Counts, TPM and FPKM

- **Give DESeq2 and edgeR raw counts.** They model counts and apply their own
  normalisation (DESeq2 median-of-ratios, edgeR TMM). Handing them TPM or
  FPKM invalidates the dispersion model.
- TPM is for comparing genes within a sample, and for display. FPKM is not
  comparable across samples in general. Neither belongs in a DE model.
- limma-voom takes counts and log-transforms with precision weights; that is
  a third valid route, strong when the design is complex.

## Design

Write the design formula explicitly and put the nuisance variables in it:
`~ batch + sex + condition`, with the variable of interest last for the
default contrast. Confounded batch and condition cannot be fixed here — say
so. Where samples are paired or repeated, use the subject term
(`~ subject + condition`) or `duplicateCorrelation` in limma, not independent
tests.

Replication beats depth for DE: more biological replicates at moderate depth
detect more real genes than fewer, deeper libraries. Three per group is the
usual floor and is a weak floor.

## Testing across genes

Every gene is a test. Report BH-adjusted p-values (`stats_multiplicity`
method `bh`) and state the family — "20,412 genes with non-zero counts after
independent filtering". Independent filtering (DESeq2 does it by default)
changes the family size; do not report the pre-filter number.

Use shrunken log fold changes (`lfcShrink`) for ranking and plotting;
unshrunken LFCs for low-count genes are wild. Apply an effect-size threshold
as well as an FDR threshold when the biology needs one — `lfcThreshold` tests
it properly, rather than filtering after the fact.

## Enrichment, honestly

GO/KEGG/GSEA results depend on the background gene set. Use the genes that
were actually testable in this experiment as the universe, not all annotated
genes. Report the database and its version, correct across terms, and treat
overlapping terms as one finding rather than ten. Enrichment is a hypothesis
generator; do not describe a pathway as "activated" from expression alone.

## Reporting

The counts matrix, the sample metadata table, the design formula, the
software versions and the full results table (not just the significant rows)
are the deliverables. Deposit raw reads and processed counts; run the whole
thing through `analysis_run` so the ledger holds the input hashes.

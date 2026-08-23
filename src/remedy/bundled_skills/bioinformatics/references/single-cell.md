# Single-cell RNA-seq

## Preprocessing

Counts come from CellRanger, STARsolo, `alevin-fry` or `kallisto|bustools`.
Record the tool, its version and the reference package — CellRanger reference
builds are not interchangeable between versions.

Then, in order, with the numbers recorded:

1. **Empty droplets** — the knee plot is a heuristic; `emptyDrops` tests it.
   Report how many barcodes were called cells.
2. **Ambient RNA** — `SoupX` or `CellBender`. Ambient contamination makes
   marker genes appear everywhere and is the usual cause of "this cluster
   expresses everything".
3. **Doublets** — `Scrublet`, `DoubletFinder`, `scDblFinder`, or genetic
   demultiplexing (`souporcell`, `vireo`) when samples were pooled. Doublets
   masquerade as intermediate or transitional cell states; a "novel hybrid
   population" is a doublet until proven otherwise.
4. **QC filtering** — minimum genes/UMIs, maximum mitochondrial percentage.
   Set thresholds per dataset from the distributions, not from a remembered
   default; a high mito fraction is real biology in some tissues. Record the
   thresholds and the number of cells lost at each step.

## Normalisation and embedding

Log-normalisation with a size factor, or `sctransform`, or scran pooled
factors — all defensible, all give different clusters. State which. Highly
variable gene selection, PCA, then neighbours and UMAP/t-SNE.

UMAP is a visualisation, not a measurement. Distances between clusters,
apparent trajectories and cluster shapes on a UMAP are not evidence. Never
draw a conclusion from the picture that is not also supported by the
underlying counts.

## Batch integration

`Harmony`, `scVI`, Seurat RPCA/CCA, `BBKNN`, `fastMNN`. Integration removes
variation between batches — including real biological differences that happen
to align with batch. Show the data before and after, report the metric used
to judge it, and do not integrate away the effect you set out to measure.

## The circularity that invalidates most p-values here

Clustering the cells, then testing which genes differ between clusters, then
reporting those p-values is double dipping: the clusters were defined by
those genes. The p-values are not valid and the marker list is a description,
not a test. Say so, and use the markers as labels rather than findings.

For a claim about **conditions** (treated vs control, patient vs healthy):

- **Pseudobulk.** Sum counts per gene per sample per cell type, then run
  DESeq2/edgeR at the sample level. The unit of replication is the sample,
  not the cell — thousands of cells from three mice is still n = 3.
- Report the number of samples and the number of cells separately.
- Cell-level tests treat cells as independent replicates and produce
  spectacular, meaningless p-values.

## Cell type labels

Manual marker-based labelling and reference-based tools (`SingleR`,
`Azimuth`, `CellTypist`) both need the reference named and the confidence
reported. Unassignable clusters stay unassigned; do not name a cluster from a
single marker.

## Reporting

Deposit the raw reads and the cell-by-gene matrix with the barcode and
feature files. Record every filtering threshold, every cell count, the random
seeds (UMAP and clustering are seed-dependent) and the software versions.

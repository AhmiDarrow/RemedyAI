# Multiple comparisons on brain data

## Count the real search space

Not just voxels: voxels x contrasts x time windows x frequency bands x
smoothing kernels x groups. A whole-brain analysis is ~10^5 tests; a
time-frequency EEG analysis is channels x times x frequencies. `p < 0.001
uncorrected` on that space yields dozens of "significant" clusters in pure
noise, which is exactly what the salmon-in-a-scanner demonstration made
famous. State the space you corrected over, and that it includes every
contrast you looked at.

## Methods

- **Voxel-wise FWE** (Bonferroni, or random field theory in SPM): strong
  control, conservative, valid when smoothness assumptions hold. RFT wants
  adequate smoothing and reasonable residual normality.
- **FDR** (Benjamini-Hochberg via `stats_multiplicity(method="bh")`):
  controls the expected proportion of false discoveries among rejections —
  a weaker, different guarantee. Say which you controlled; they are not
  interchangeable.
- **Permutation / non-parametric** (FSL randomise, SnPM, MNE cluster tests):
  the reliable default. Build the null by shuffling the exchangeable labels,
  respecting the exchangeability structure (permute within subject for
  repeated measures, permute subject-level signs for one-sample designs).
- **TFCE**: avoids picking an arbitrary cluster-forming threshold and is
  usually preferable to cluster extent.
- **Small-volume correction** is legitimate only when the volume was fixed
  before looking at the data, and the volume and its source are reported.

## Cluster-extent inflation

Cluster-based inference with a lenient cluster-forming threshold (the old
p < 0.01 habit) has an inflated false-positive rate under parametric
assumptions, because the spatial autocorrelation of fMRI residuals is not
the assumed Gaussian shape. Practical rules: use a strict cluster-forming
threshold (p < 0.001) with parametric methods, or use permutation/TFCE and
avoid the choice; report the threshold, the correction, the cluster extent
and the corrected p for every cluster.

**A cluster-level p does not localise.** It says the cluster as a whole is
unlikely under the null; it does not license "the peak voxel at (x,y,z) is
active" or a claim about a sub-region within the cluster. Report peaks as
descriptive coordinates and label them by an atlas you name.

## EEG/MEG specifics

Cluster-based permutation over channels x time (x frequency) is standard and
has the same caveat: the test is of the cluster, not of its boundaries, so
"the effect began at 180 ms" is not supported by a cluster spanning
150-400 ms. Pre-specify the time window and channel set where theory allows;
otherwise correct across all of it.

## Reporting

Method, software and version, search space, cluster-forming threshold,
number of permutations, corrected p per cluster, effect size with interval,
and the unthresholded map deposited (NeuroVault). If a result only survives
uncorrected, call it exploratory in the text and the abstract.

# Connectivity: what it does and does not license

Three different things, routinely conflated in one sentence:

- **Structural** — physical connections. Estimated in humans by diffusion
  tractography, which is a model fit to water displacement, not axons.
- **Functional** — statistical dependence between time series (correlation,
  coherence, mutual information). Symmetric, undirected, and agnostic about
  mechanism.
- **Effective** — directed influence under an explicit model (DCM, Granger,
  transfer entropy, or a causal perturbation).

A functional result never upgrades itself into an effective one, and neither
implies a monosynaptic connection: shared input, common drive, a slow
physiological rhythm or one shared artefact correlates two regions with no
direct link.

## Artefacts that manufacture connectivity

- **Motion** raises short-distance and lowers long-distance correlations in
  fMRI, and motion differs between groups — that alone has produced
  published group "connectivity differences". Report FD per group, match or
  censor, and show the result survives.
- **Respiration and cardiac** cycles add global structure; physiological
  regressors or multi-echo denoising help.
- **Global signal regression** forces the correlation distribution negative
  and can flip a group difference. Report with and without.
- **Volume conduction / field spread** in EEG and MEG correlates nearby
  sensors and sources at zero lag. Use measures insensitive to zero-lag
  mixing (imaginary coherence, phase-lag index, orthogonalised power
  envelopes) and say which. Source-space connectivity between adjacent
  parcels is partly the inverse solution talking to itself.
- **Common reference** in EEG couples every channel; the referencing scheme
  changes every value.
- Short windows and unequal trial counts bias coherence and phase-locking
  upward; match them.

## Directed methods

Granger causality tests predictability, not causation; unequal
signal-to-noise or haemodynamic lag between regions can invert the reported
direction. DCM compares pre-specified models — its output is which of
*those* fits best, so state the model space. Transfer entropy needs a lot of
data and careful embedding.

The strong evidence for directed influence is perturbation: stimulate or
inactivate one node and measure the other, with the controls in
behaviour-and-controls.md.

## Tractography limits

Crossing, kissing and fanning fibres, gyral bias, and no polarity: a
streamline is not an axon and tractography cannot tell afferent from
efferent. Long-range false positives are common. Report the model (tensor vs
CSD), the seeding and stopping rules, and validate against known anatomy.
Streamline counts are counts under a model, not "connection strength".

## Reporting

Name the measure, the frequency band, the window, the parcellation and its
version, the denoising, and the network-level correction applied over the
edges tested (`stats_multiplicity`, or a network-based statistic).

# Modalities: what the signal actually is

## fMRI (BOLD)

Deoxyhaemoglobin-weighted signal driven by blood flow, volume and oxygen
use. It follows neural activity through a haemodynamic response peaking
around 4-6 s, so BOLD timing differences between regions can reflect
vasculature, not order of processing. It tracks local field potentials
(input and local processing) better than spiking output. Resolution:
millimetres, seconds. Signal drops out near sinuses (orbitofrontal, anterior
temporal) — check coverage before claiming a null there. Physiological noise
is large relative to the effect, which in a typical contrast is under a
percent signal change.

## EEG and MEG

Summed postsynaptic currents from synchronously active, similarly oriented
pyramidal populations. Millisecond timing is their whole advantage. EEG is
distorted by skull and scalp conductivity and depends on the reference; MEG
is reference-free and blind to purely radial sources. Both face an ill-posed
**inverse problem**: infinitely many source configurations fit one sensor
pattern, so a source estimate is model-dependent — state the head model,
source space and regularisation, and never present a source map as
localisation fact. Scalp topography is not anatomy.

## Extracellular electrophysiology

- **Spikes**: single or multi-unit action potentials, sub-millisecond, at
  cellular scale — the only direct measure of output. Sorting quality
  decides everything downstream (see spikes-and-population-analysis.md).
- **LFP**: low-frequency field reflecting synaptic input and local
  processing, pooled over a poorly defined, frequency-dependent volume of
  hundreds of microns to millimetres. LFP power is not "the activity of this
  nucleus".
- Chronic recordings drift: a channel is not the same neuron across days
  unless you demonstrate it (waveform and ISI stability).

## Calcium and voltage imaging

Genetically encoded calcium indicators (GCaMP family) report intracellular
calcium, a **nonlinear, low-pass filtered proxy for spiking**: slow rise,
slower decay, poor at high firing rates, and can miss single spikes. Report
the indicator, expression time, imaging rate and spike inference method —
deconvolution outputs are estimates, not spike trains.
Voltage indicators trade signal-to-noise for speed. Two-photon gives
cellular resolution over a limited field and depth; widefield mixes neuropil
across millimetres and carries haemodynamic contamination.

## Optogenetics and chemogenetics

Manipulation, not measurement; the causal claim rests on the controls:
opsin-negative animals given the same light, light-only and fluorophore-only
groups, verified expression extent, measured irradiance and illuminated
volume, and awareness that stimulation drives antidromic and downstream
structures. Chemogenetics needs a ligand-only control, since the ligand and
its metabolites act on their own. Say what was stimulated, at what rate, for
how long — "activating region X" is a summary, not a method.

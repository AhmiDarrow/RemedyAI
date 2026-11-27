# Randomisation, allocation concealment, blinding

Three different things. Papers confuse them; do not.

## 1. Sequence generation

- Computer pseudo-random with a **recorded seed** and a saved script, or a
  central randomisation service. Alternation, date of birth, chart number,
  day of the week and "whoever is next" are not randomisation.
- **Simple**: a coin flip per participant. Fine above roughly 200; below
  that arm sizes can drift badly.
- **Permuted blocks**: guarantees balance. Vary the block size at random and
  keep the sizes concealed, or an unblinded recruiter can predict the last
  slot in a block.
- **Stratified**: block within a small number of strong prognostic factors
  (site is almost always one). Do not stratify on many factors at once —
  strata empty out.
- **Minimisation**: allocates to balance covariates dynamically, with a
  random element retained. Powerful for small trials; needs a central system
  and pre-specified factors.
- Unequal allocation (2:1) is legitimate and costs power; state the ratio.

## 2. Allocation concealment

Whoever enrols must not be able to foresee the next assignment. Central
web/telephone allocation, or sequentially numbered, opaque, sealed envelopes
prepared by someone with no contact with participants. This protects against
selection bias and is achievable in **every** trial, including open-label
ones. Report the mechanism explicitly; "randomised" alone is not evidence.

## 3. Blinding (masking)

Name each group separately: participants, care providers, outcome assessors,
data analysts. "Double-blind" is ambiguous — list who was blinded.

- Placebo or sham comparator must match in appearance, taste, smell, route,
  schedule and packaging, and its composition is reported.
- **When blinding is impossible** (surgery, physiotherapy, behavioural,
  device): blind whoever you still can — usually the outcome assessor and
  the analyst — and prefer objective or adjudicated endpoints (mortality,
  laboratory values, imaging read by a blinded committee). Say plainly in
  the limitations that participant-reported outcomes are open to
  expectation effects.
- Keep the analyst blind by coding arms as A/B until the primary analysis is
  locked and signed off.
- Emergency unblinding needs a documented procedure; every unblinding event
  is logged and reported.

## Checks and reporting

- A blinding-success question can be asked, but interpret it cautiously:
  guesses track perceived benefit as much as unmasking. Report the answers
  rather than declaring blinding intact.
- Report: who generated the sequence, who enrolled, who assigned, the
  concealment mechanism, who was blinded, similarity of the placebo, and any
  unblinding — this is what the CONSORT items ask for.
- Baseline imbalance after correct randomisation is chance. Do **not** run
  significance tests on baseline tables; pre-specify any adjusted analysis
  instead.

# CMIP, scenarios and ensemble spread

## A model number is meaningless without its label

Record and report, every time: project (CMIP5/CMIP6), experiment (historical,
ssp245, piControl, amip), source_id (the model), variant label
(r1i1p1f1 — realisation, initialisation, physics, forcing), grid label (gn
native / gr regridded), table (Amon, day, Omon), variable and version date.
These are attributes in the file; read them rather than trusting the filename.

Scenario families: CMIP5 uses RCPs (radiative forcing pathways); CMIP6 uses
SSP-RCP combinations (ssp126, ssp245, ssp370, ssp585) pairing a socio-economic
pathway with a forcing level. They are *scenarios*, not forecasts and not
equally likely; never present one as "what will happen". Confirm scenario
definitions from the project documentation rather than reciting them.

## Kinds of spread — do not mix them

- **Internal variability** — different initial conditions, same model. Estimated
  from a single-model initial-condition large ensemble.
- **Model uncertainty** — structural differences between models.
- **Scenario uncertainty** — different forcing pathways.
Their relative size depends on lead time and variable: internal variability
dominates the near term and small regions; scenario uncertainty dominates the
late century. Say which one your error bar represents.

## Weighting and independence

Models are not independent draws — they share components, code and tuning
lineage. A multi-model mean is not an unbiased estimate of truth, and "more
models agree" is not more evidence when they share a parameterisation. Report
model spread (range, interquartile range, individual models) alongside any
central estimate, and if you weight models, state the criteria and show the
unweighted result too.

## Common handling errors

- Averaging across variants without saying how many each model contributed —
  one model with 30 realisations dominates the multi-model mean.
- Comparing model output on native grids of different resolution without
  regridding to a common grid, conservatively for fluxes.
- Comparing model absolute values with observations where anomalies were the
  intended comparison; models have known mean biases.
- Applying bias correction and then interpreting the corrected extreme tail as
  physically simulated. Say the correction method and its assumptions.
- Using a scenario period against a baseline period from a different experiment
  without checking the forcing continuity at the splice year.

## Reporting

Say the model list, the number of realisations per model, the regridding, the
baseline, the scenario and the period, and give the spread. Cite the datasets by
their DOIs (ESGF assigns them) with `cite_add`, and cite the model description
papers the modelling centres nominate — do not invent a citation for a model.
Verify the file list and hashes are in the run ledger so the exact ensemble is
recoverable.

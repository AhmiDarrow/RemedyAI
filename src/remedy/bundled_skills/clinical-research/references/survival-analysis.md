# Time-to-event analysis

## Set up the clock first

Define, in the protocol: time zero (randomisation, diagnosis, first dose),
the event, and every reason for censoring. Time zero must be the same event
for everyone and must not depend on the future.

- **Right censoring** (still event-free at last contact) is what standard
  methods assume, and they assume it is **non-informative** — censoring
  unrelated to the hazard. Dropout because a patient is deteriorating breaks
  that. State the assumption and test it with a sensitivity analysis
  (censor-at-worst / censor-at-best bounds).
- **Competing risks**: death from another cause does not censor, it prevents
  the event. Kaplan-Meier then overstates incidence — use the cumulative
  incidence function (Aalen-Johansen) and a Fine-Gray or cause-specific Cox
  model, and say which question each answers (a subdistribution hazard is
  not a cause-specific hazard).
- **Immortal time bias**: classifying exposure by something that can only
  happen after time zero (received a transplant, responded) guarantees the
  exposed group survives long enough to qualify. Fix with a landmark
  analysis or a time-varying covariate.
- **Interval censoring** (events found only at scheduled visits) needs
  interval-censored methods; treating the visit date as the event date
  biases toward the visit schedule.

## Estimating and comparing

- **Kaplan-Meier** for the survival curve. Always print the numbers at risk
  under the axis, and stop the curve where the risk set becomes thin — the
  tail of a KM curve with 3 people left is noise. Median survival with its
  CI; if the curve does not reach 0.5, say "not reached", never extrapolate.
- **Log-rank** compares whole curves and is most powerful under
  proportional hazards. Crossing curves make it lose power; consider a
  weighted test (Fleming-Harrington) or restricted mean survival time
  (RMST), which needs no PH assumption and reports in months of life.
- **Cox model** for adjusted hazard ratios. Report the HR with a CI, the
  events per arm, and the covariates — and remember the HR is an average
  over the follow-up period, not a constant fact.

## Checking proportional hazards (do it, and report it)

1. Scaled Schoenfeld residuals against time: slope not different from zero.
2. Log(-log(S)) plots by group: roughly parallel.
3. A treatment-by-time interaction term: not significant.

If PH fails, do not shrug. Options: stratify on the offending covariate,
model a time-varying coefficient, report RMST differences at fixed
horizons, or split follow-up into periods — and say which was chosen and
why.

## Reporting

Median follow-up (reverse KM, not mean time observed), events/N per arm,
absolute risk at clinically meaningful timepoints, HR with CI, the PH check,
and the censoring pattern per arm. `stats_effect_size` for the absolute
measures — risk difference and NNT communicate what an HR does not.

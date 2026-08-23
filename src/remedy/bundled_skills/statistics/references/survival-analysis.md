# Survival / time-to-event analysis

Use it whenever the outcome is *time until* something, and some subjects
have not had it yet. Analysing "proportion who had the event by the end"
throws away the timing and mishandles anyone who left early.

## Censoring is the whole point

- **Right censoring** — event-free when last observed (study ended,
  withdrawal, lost to follow-up). Standard methods assume it is
  **non-informative**: censoring carries no information about the hazard.
  Dropping out *because* of deterioration breaks that. Say which kind of
  censoring the data has and why non-informativeness is plausible.
- **Left truncation / delayed entry** — subjects only enter the risk set
  after some time (e.g. registry data). Ignoring it biases estimates;
  handle with entry/exit times, not by shifting the clock.
- **Competing risks** — death from another cause is not censoring. Use
  cumulative incidence functions and Fine-Gray, not Kaplan-Meier, when a
  competing event prevents the event of interest.
- Define **time zero** explicitly and identically for everyone
  (randomisation, diagnosis, first dose). Time zero chosen after exposure
  is known produces immortal time bias.

## The standard sequence

1. **Kaplan-Meier** curves per group, with the number-at-risk table under
   the x-axis and confidence bands. Report median survival with its CI
   (and say "not reached" when it is not reached — do not extrapolate).
2. **Log-rank test** for a difference across curves. It is most powerful
   under proportional hazards and weak when curves cross. Report it as a
   test of the curves, not of a hazard ratio.
3. **Cox proportional-hazards model** for covariate adjustment. Report the
   hazard ratio with its CI, the reference level, and the number of
   events (events, not n, drive the power).
4. **Parametric / AFT models** (Weibull, exponential) when you need
   extrapolation or a time-scale reading; an AFT coefficient is a time
   ratio, which is often easier to explain.

## Proportional hazards is an assumption to check

Test Schoenfeld residuals against time (global and per covariate) and
look at log(-log(S)) plots for parallelism. When it fails: stratify on
the offending covariate, add a time-varying coefficient, split follow-up
into periods, or switch to AFT or restricted-mean. Never report a single
HR from a model whose PH assumption failed — it averages a changing
effect and hides a crossover.

**Restricted mean survival time (RMST)** up to a stated horizon is a good
assumption-free alternative and reads in units the owner understands
("4.2 months longer over 5 years").

## Power and reporting

Power depends on the **number of events**, so size the study in events,
with accrual and follow-up stated. Report n, events per group, median
follow-up (reverse KM), the estimator, the HR/RMST with CI, and how
censoring was handled.

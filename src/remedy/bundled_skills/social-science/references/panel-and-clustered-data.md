# Panel and clustered data

## Find the clustering before choosing a model

Ask what shares an error term: repeated measures on a person, pupils in classes
in schools, respondents in villages, firms over time, items within a survey.
Ignoring it produces standard errors that are too small and p-values that look
better than the data supports.

`data_profile` on the id columns first: number of clusters, cluster sizes,
balance, and duplicated (id, time) rows — a duplicated key silently changes
every within-unit estimate.

## Fixed effects vs random effects

- **Fixed effects** absorb all time-invariant differences between units and
  identify from within-unit variation only, so a variable that never changes
  within a unit cannot be estimated. Usually the safer default when unobserved
  unit heterogeneity is the worry.
- **Random effects / multilevel models** are more efficient and can estimate
  between-unit effects, but assume the unit effect is uncorrelated with the
  regressors. Say which assumption you are relying on rather than citing a
  Hausman test as if it settled it.
- Multilevel models are the right home for cross-level interactions, varying
  slopes and partial pooling. Group-mean centring changes what a coefficient
  means (within vs between); state the centring.
- With few clusters (rule of thumb: under about 30-50, and it is a rule of
  thumb), cluster-robust standard errors are anti-conservative. Use a wild
  cluster bootstrap or a small-sample correction, and report which.

## Clustering the standard errors

Cluster at the level of treatment assignment or of sampling — usually the
coarser one, not the finer. Two-way clustering when two crossed dependencies
exist (unit and time). Report the cluster variable and the number of clusters in
the table note; a result that survives at only one clustering level is fragile.

## Time structure

- Check stationarity and serial correlation before interpreting lagged models.
- Lagged dependent variables plus fixed effects produce bias in short panels
  (Nickell bias); use a GMM-style estimator or say why the bias is tolerable.
- Age, period and cohort effects are collinear by construction and cannot all
  be identified without an assumption. State it.
- Attrition in a panel is non-random by default: report retention by wave and
  test whether baseline outcome predicts dropout.

## Repeated measures within person

Within-person designs remove all stable individual differences and are usually
far better powered. Use `power_analysis(test="paired_t")` or the design-effect
route with `clusters` and `icc`. Watch for order and carryover effects; randomise
or counterbalance condition order and record it.

## Reporting

Model, estimand, clustering level, number of clusters and observations, whether
effects are within or between, and a sensitivity table across the plausible
specifications. Run every model through `analysis_run` so the specification is
recoverable from the ledger.

# Systematic reviews and meta-analysis

A review is a study. It gets a protocol, a registration and a reproducible
search, or it is a narrative essay with a table.

## Protocol first

1. Frame the question as PICO (population, intervention/exposure,
   comparator, outcome) plus designs and timeframe. Pre-specify the primary
   outcome — the same discipline as a trial.
2. Register in **PROSPERO** (or OSF for reviews it does not accept) before
   screening starts. Report the registration id.
3. Write the protocol to the PRISMA-P shape; report later deviations as
   deviations.

## Search

- At least two databases plus a trials register (MEDLINE/PubMed via
  `lit_search(source="pubmed")`, Embase, Cochrane CENTRAL,
  ClinicalTrials.gov; add CINAHL, PsycINFO, Scopus by topic), then backward
  and forward citation chasing and preprint servers.
- Record the **full Boolean strategy per database, with dates and hit
  counts** in an appendix; a search that cannot be re-run is not systematic.
  Save exported records with `cite_import`.
- Grey literature and unpublished trials matter because that is where null
  results hide. Note language restrictions as a limitation.

## Screening and extraction

- Two independent screeners at title/abstract and full text, disagreements
  resolved by a third reviewer. Report agreement (Cohen kappa) and the
  reasons for full-text exclusion.
- Duplicate, piloted extraction into a pre-designed form. Contact authors
  for missing data and record who answered.
- Build the PRISMA flow diagram from the actual counts: identified →
  duplicates removed → screened → full text assessed → included, with
  exclusion reasons at full text.

## Risk of bias

Per outcome, per study, with the tool the design calls for: Cochrane RoB 2
for randomised trials, ROBINS-I for non-randomised interventions, QUADAS-2
for diagnostic accuracy, with domain judgements and the supporting quote.
Study quality is not a score to sum — report the domains.

## Synthesis

- Pool only what is clinically and methodologically poolable; if it is not,
  synthesise without meta-analysis (SWiM) and say why.
- Random-effects is usually the honest default; report tau-squared, I² and
  the **prediction interval** — I² is a proportion of variability, not an
  amount of heterogeneity, and a tight CI around a heterogeneous pool
  misleads.
- Pre-specify subgroup and sensitivity analyses; subgroup differences need
  an interaction test, not two separate p-values.
- Funnel plot and Egger test only with roughly ten or more studies, and read
  them as small-study effects, not proven publication bias.
- Rate certainty per outcome with **GRADE** (risk of bias, inconsistency,
  indirectness, imprecision, publication bias); present a summary-of-findings
  table with absolute effects.

## Before it is done

`manuscript_check(checklist="prisma")` for the reporting items, then
`cite_check(manuscript, resolve=True)` must PASS — every included study is a
citation that has to resolve.

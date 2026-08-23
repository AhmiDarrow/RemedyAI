---
name: social-science
description: >
  Use when a claim is being made about people from survey, administrative,
  behavioural or interview data: sampling and what it licenses, instrument and
  scale design, measurement validity and reliability, experiments versus
  quasi-experiments, causal inference without randomisation (DAGs, DiD, IV,
  RDD, matching), panel and clustered data, preregistration, and the consent
  and data-protection duties that come with human participants.
version: 1.0.0
author: Remedy
tags: [research, social-science, survey, psychometrics, causal-inference, ethics]
requires: []
tools: [power_analysis, stats_assumptions, stats_effect_size, stats_multiplicity, data_profile, analysis_run, analysis_ledger, manuscript_check, lit_search, cite_add, skill_activate, file_read, file_write]
triggers:
  - '\b(survey (?:instrument|design|weights?)|Likert (?:scale|items?)|questionnaire (?:validity|items?)|sampling frame)\b'
  - '\b(difference[- ]in[- ]differences|instrumental variables?|regression discontinuity|propensity scores?|fixed effects? model)\b'
  - '\b(construct validity|internal validity|external validity|Cronbach.?s alpha|inter[- ]rater reliability|Cohen.?s kappa)\b'
  - '\b(qualitative coding|thematic analysis|grounded theory|ethnograph\w+|focus groups?|semi-?structured interviews?)\b'
---

# Social science method

Run `skill_activate(skill="research-method")` first and work from that spine —
question framing, evidence standards, preregistration, citation honesty, how to
say "we do not know". Do not restate it. This pack covers only what changes when
the units of analysis are people: they can guess the hypothesis, refuse, drop
out, be measured badly, and cannot usually be randomised.

## Decision tree

1. **Name the claim type before touching data.** Descriptive ("how many, how
   often"), measurement ("does this instrument capture the construct"), or
   causal ("does X change Y"). Most arguments about a social-science result are
   a descriptive design carrying a causal sentence. Write the sentence the
   design can support, and keep the manuscript inside it.
2. **Write the sampling story before the n.** Target population, sampling frame,
   selection mechanism, response rate, weights. A convenience sample licenses
   "in this sample"; anything wider is an argument you have to make, not a
   default. `references/sampling-and-generalisation.md`.
3. **If you are measuring a construct, treat the instrument as the experiment.**
   Prefer a published validated instrument over new items, and say which
   version and which population it was validated in. New items need pilot,
   cognitive interviews and a reliability estimate.
   `references/survey-and-instrument-design.md`,
   `references/measurement-validity.md`.
4. **Randomised?** If yes, the design carries the causal claim; check balance,
   attrition and compliance, and analyse as randomised (ITT). If no, pick an
   identification strategy on purpose and state its untestable assumption in
   the abstract, not a footnote. `references/causal-inference-toolkit.md`.
5. **Draw the DAG before choosing controls.** Adjust for confounders; never
   condition on a collider or a mediator you also want the total effect of.
   "Control for everything available" is a bug, not caution.
6. **Check the data structure.** Repeated measures, students in classrooms,
   respondents in countries, the same person over time — clustering changes the
   standard errors and often the effective n.
   `references/panel-and-clustered-data.md`.
7. **Preregister, then keep the diff.** Deviations are allowed and normal; hiding
   them is not. `references/preregistration-and-replication.md`.
8. **Ethics is a procedure step, not a footer.** Consent, minimal data,
   de-identification, storage and retention are decided before collection.
   `references/human-subjects-ethics.md`.

Read `references/INDEX.md` and pull what you need with `file_read`.

## Running it

- `data_profile(path, target=...)` first: missingness patterns, straightlining,
  duplicated respondent ids, out-of-range codes (-99, 999), class balance.
  Treat its leakage rows as suspects to check, never as verdicts.
- `power_analysis(...)` **before** collection, with the effect the owner is
  willing to defend as the smallest one worth detecting — not the effect from a
  small published study, which is biased upward by publication filtering.
  Inflate for `dropout`, and for clustered designs pass `clusters` and `icc`.
- `stats_assumptions(data_path, outcome=, group=, design=)` to choose the test
  from the data rather than habit; read `what_this_cannot_tell_you` — it will
  tell you independence is a design question, and it is right.
- `stats_effect_size(...)` for every reported comparison, with the interval. Say
  what the effect means in the outcome's own units; refuse "small/medium/large"
  as if it were a fact about the world.
- `stats_multiplicity(pvalues=..., method="holm")` when the analysis implies a
  family of tests — many outcomes, many subgroups, many specifications. Adjusting
  after seeing which ones worked is not the same as planning them.
- Every model run goes through `analysis_run` so the argv, input hashes and
  artifacts land in the ledger; `analysis_ledger(action="verify", run_id=...)`
  before a number goes into the manuscript.

## Qualitative work

It is evidence, judged by different standards, and it is not "colour" for a
quantitative paper. Sampling logic, saturation, codebook, multiple coders where
the claim is a count, reflexivity and an audit trail are the rigour — say which
ones you did. `references/qualitative-methods.md`.

## What counts as verified here

A social-science claim is verified when all of these hold, and you say which one
failed when one does:

1. The analysis reruns end to end from raw data with `analysis_run` and produces
   the reported numbers — check with `analysis_ledger(action="verify")`.
2. The reported estimate is the preregistered one, or the deviation is stated.
3. The estimate is reported with an interval and an effect size in real units,
   and the identification assumption is named in the text.
4. Robustness is shown, not asserted: alternative specifications, clustering
   level, exclusion rules. A specification curve beats one lucky model.
5. `cite_check(manuscript, resolve=True)` returns PASS.
6. `manuscript_check(path)` for a reporting checklist where one applies
   (STROBE-family for observational human work); it reports evidence found, not
   compliance, and a human confirms it.

State the limits of external validity in the paper. One WEIRD sample, one
platform, one year, one country is a real finding about that sample and an open
question about everyone else.

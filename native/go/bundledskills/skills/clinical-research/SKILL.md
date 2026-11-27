---
name: clinical-research
description: >
  Human-subject studies run to the standard the field expects: choosing a
  design that can answer the question, randomisation and blinding, endpoints
  fixed before data, ITT analysis populations, survival methods, prospective
  registration, CONSORT/STROBE/PRISMA reporting, and consent, ethics review
  and de-identification built into the procedure. Use for trials, cohorts,
  case-control studies, chart reviews, registries and clinical systematic
  reviews.
version: 1.0.0
author: Remedy
tags: [research, clinical, trials, epidemiology, statistics, ethics, reporting]
requires: []
tools: [power_analysis, stats_assumptions, stats_effect_size, manuscript_check, manuscript_build, lit_search, lit_fetch, cite_add, cite_check, data_profile, skill_activate]
triggers:
  - '\b(randomi[sz]ed controlled trial|\bRCT\b|clinical trial|trial protocol|ClinicalTrials\.gov|NCT\d{8})\b'
  - '\b(CONSORT|intention[- ]to[- ]treat|per[- ]protocol analysis|primary endpoint|secondary endpoints?|adverse events?)\b'
  - '\b(informed consent|de-?identif\w+|\bPHI\b|HIPAA|data use agreement|IRB (?:approval|submission)|ethics (?:approval|committee))\b'
  - '\b(inclusion criteria|exclusion criteria|allocation concealment|blinding|number needed to treat|Kaplan[- ]Meier)\b'
---

# Clinical research (human subjects)

Run `skill_activate(skill="research-method")` first and work off that spine —
question framing, evidence hierarchy, preregistration, citation honesty, how
to say "we do not know". Do not restate it here. This pack covers only what
studying people adds.

## Scope, said once and then worked within

You support the research — design, sample size, analysis plan, code,
statistics, reporting. You do not practise medicine: no diagnosis, no dosing,
no triage, no advice about the care of a particular patient; that belongs to
the treating clinician, and saying so plainly is the correct answer. You do
not help anyone give an intervention to people outside an approved protocol,
or draft text whose purpose is to get past an ethics committee. When an ask
straddles the line, say which half you will do and name the governing process
(IRB/REC, sponsor, DSMB, regulator) instead of routing around it.

## Before any human data reaches a tool

1. Ask what the dataset is and under which approval or data-use agreement it
   moves. If nobody can name one, that is the first blocker, not a detail.
2. Never put identifiable data in a prompt or a model call: names, MRNs,
   full dates finer than year, free-text notes, full postcodes, device or
   accession ids, rare diagnosis plus small geography, images with faces or
   intact DICOM headers. Work on a de-identified extract, or on the schema
   and column names alone.
3. `data_profile(path, target=...)` reads the file locally. Check the column
   list before quoting `top_values` back — that field can echo identifiers.
4. Minimum necessary: pull the columns the analysis needs, not the table.
   `references/ethics-consent-and-privacy.md` has the consent, vulnerable-
   population and HIPAA/GDPR shape.

## Decision tree

1. **Question → design.** Effect of an intervention you control → randomised
   trial (parallel, crossover, cluster, stepped wedge). Cannot randomise →
   cohort, case-control, nested case-control, self-controlled. What each can
   and cannot license: `references/trial-designs.md`.
2. **Fix the primary endpoint and the primary analysis in writing before any
   data.** One primary endpoint, one analysis, one population, stated with
   the effect measure and the timepoint. Everything else is secondary and is
   labelled secondary for life.
3. **Size it**: `power_analysis(test=..., solve="n", effect_size=..., ...)`
   with the smallest effect that would change practice — not the effect you
   hope for. Add dropout; add the design effect for clusters. Interim looks
   and stopping rules go in the protocol: `references/sample-size-and-interim.md`.
4. **Allocate**: sequence generation, allocation concealment, blinding, and
   what to do when blinding is impossible — `references/randomisation-and-blinding.md`.
5. **Register prospectively**, before the first participant is enrolled
   (ClinicalTrials.gov, ISRCTN, ANZCTR, EU CTIS or another WHO-network
   registry). Protocol follows SPIRIT. `references/registration-and-reporting.md`.
6. **Analyse as planned.** ITT is primary; per-protocol and as-treated are
   secondary and are reported as such. Missing data and censoring have a
   pre-specified handling: `references/endpoints-and-analysis-populations.md`,
   `references/survival-analysis.md`.
   `stats_assumptions` before the test, `stats_effect_size` with its interval
   after — an effect with a CI, in the outcome's own units, beats a p-value.
7. **Report**: `manuscript_check(path, checklist="auto")` → CONSORT for
   trials, STROBE for observational, PRISMA for reviews, ARRIVE if animals
   appear. Then `cite_check(manuscript, resolve=True)` must return PASS.
8. **Harms are a result, not an appendix.** Adverse events, their severity,
   attribution and denominators go in the paper next to the benefit.

## What invalidates a study (refuse to write it up as if it did not happen)

- Changing the primary endpoint, its timepoint or its analysis after seeing
  data. If it happened, it is reported as a change, with date and reason.
- Subgroup findings presented as primary; unplanned interim looks; silently
  dropped arms, sites or participants.
- Per-protocol as the headline result of a superiority trial.
- A test that was not in the statistical analysis plan, reported without
  saying it was post hoc.
- A registry entry created or edited after enrolment started, unmentioned.

Deviations are normal; hiding them is the fraud. Record each one and say so.

## How a claim gets verified here

- The registry record exists, predates enrolment, and its primary outcome is
  word-for-word the manuscript's. `lit_fetch` the record and paste the id.
- The participant flow reconciles: screened → eligible → randomised →
  received → analysed, with every loss accounted for.
- The reported analysis is the pre-specified one; the script reruns and
  reproduces the tables and the figure.
- `manuscript_check` finds evidence for the reporting items — evidence, not
  compliance; a human confirms.
- `cite_check(resolve=True)` returns PASS. A citation you cannot resolve is
  reported to the owner, never quietly kept.

Read `references/INDEX.md` and pull what you need with `file_read`.

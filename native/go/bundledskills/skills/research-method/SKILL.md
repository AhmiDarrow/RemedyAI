---
name: research-method
description: >
  The research loop for any field: sharpen the question until it can be
  refuted, find what is known and what is disputed, fix the analysis before
  the data, run it, interpret against effect size and uncertainty, write it
  so someone else can repeat it, and leave the numbers reproducible. Use for
  literature reviews, hypotheses, study design, preregistration, citations
  and anything called a paper, protocol or analysis plan.
version: 1.0.0
author: Remedy
tags: [research, method, literature, citations, reproducibility, evidence]
requires: []
tools: [lit_search, lit_fetch, cite_add, cite_import, cite_list, cite_export, cite_check, analysis_ledger, manuscript_check, skill_activate, file_read, file_write, file_edit, repo_search, web_fetch]
triggers:
  - '\b(research question|literature review|systematic review|scoping review|pre-?registration|prereg\w*)\b'
  - '\b(?:null|alternative|working|research)\s+hypothes[ei]s\b|\bfalsifiab\w+\b|\bconfound(?:er|ing|s)\b'
  - '\b(reproducib\w+|replicat(?:e|ion)\s+(?:the\s+)?(?:study|analysis|package|crisis)|p-?hack\w*|HARK(?:ing)?|garden of forking paths)\b'
  - '\b(doi:\s?10\.\d{4,9}/|arXiv:\s?\d{4}\.\d{4,5}|bibtex|\.bib\b|CSL[- ]?JSON|cite\s+(?:the\s+)?(?:paper|source|literature))\b'
---

# Research method (the spine)

This pack owns the loop, the evidence standards, citation honesty and what
"reproducible" means. Field packs add only what is different in their field —
activate one alongside this, never instead of it. Do not restate the spine
inside field work; apply it.

Read `references/INDEX.md`, then `file_read` the one or two that match the
stage you are at. The stage, not the whole shelf.

## The loop

1. **Question.** Rewrite it until it names a population, an exposure or
   comparison, an outcome and a measurement. If no result could come back
   and make it wrong, it is not a research question yet — say so and fix it
   before anything else. → `references/question-and-hypothesis.md`
2. **What is known.** `lit_search` (broad, then narrowed), `lit_fetch` for the
   ones that matter. Record the search string, source, date and hit count —
   a review whose search cannot be rerun is an opinion. Separate settled,
   contested and one-lab findings; name the dispute rather than picking a
   side. → `literature-review.md`
3. **Hypothesis.** State H0, H1, the direction, and *what result would refute
   it*. Write the refutation condition down now; it is unwritable later.
4. **Design.** Units, assignment, controls, blinding, confounders, sample
   size. The analysis is decided here, before the data exists — which test,
   which covariates, which exclusions, how missing data is handled.
   → `study-design.md`, `analysis-plan.md`
5. **Preregister** where the field expects it (trials, and increasingly
   psychology, ecology, econ). A dated, read-only plan file in the repo is
   the minimum; a registry entry when one governs the work.
   → `preregistration.md`
6. **Collect / obtain**, recording provenance and version for every dataset.
7. **Analyse as planned.** Anything not in the plan is exploratory and is
   labelled exploratory in the text — not softened, labelled. Every number
   comes out of a run in the ledger, never out of a chat message.
8. **Interpret** against the effect size and its interval, in the outcome's
   own units. A p-value alone is not a result; "not significant" is not
   "no effect". → `interpreting-results.md`
9. **Write up** so a stranger could repeat it. → `writing-up.md`
10. **Leave it reproducible.** That is the verify step (below).

## Evidence standards

Weigh a source by what was actually done, then say which tier you used:

- **Established** — replicated by independent groups, or a systematic review
  / meta-analysis with a registered protocol.
- **Contested** — good studies disagree. Report the disagreement and the
  likely reason (population, dose, measure, analysis choice).
- **One-lab / preliminary** — a single study, however good. Cite it as one
  study, with its n.
- **Preprint** — real evidence, not peer reviewed. Say so at every use, and
  check for the published version before relying on it.
- **Conference abstract** — often never becomes a paper; treat as a lead.
- **Press release / news** — not evidence. Go to the paper it describes.
- **Retracted or corrected** — never cite as support. `cite_check` flags what
  it can; also check the publisher page and Retraction Watch/Crossref for
  anything load-bearing.

Preregistered + prespecified analysis outranks a same-size study analysed
after the fact. Large n does not rescue a biased design.

## Citation honesty

- Never write a reference you have not seen resolve. No DOI, PMID, arXiv id,
  author, year or title from memory — `lit_search`/`lit_fetch` it, then
  `cite_add`. If it will not resolve, it does not go in.
- Cite what you read. If you only saw the abstract, the claim must be one the
  abstract supports.
- **The gate:** no manuscript is done until `cite_check(manuscript=…,
  resolve=True)` returns PASS. `MISMATCH` and `NO_IDENTIFIER` rows are
  reported to the owner as unresolved — never quietly kept, never "fixed" by
  editing the entry to match. `cite_export(only_cited_in=…)` ships the
  minimal bib.
- Quotes are copied, never reconstructed.

## Saying "we do not know"

Say it plainly, then do one of these — and say which:

- **Underpowered / undecidable:** report the effect with its interval and the
  minimum detectable effect at this n. "The data cannot distinguish X from no
  effect" is a finding.
- **Wrong instrument:** name the measurement that would decide it.
- **Outside the literature:** say the search found nothing and give the exact
  query and sources tried so the owner can judge the gap.
- **Beyond your reach:** an instrument, a cohort, an ethics approval — hand it
  to the owner with what is needed.

Never fill a gap with a plausible sentence. An unverified claim gets a
`[verify: <exact tool call or command>]` marker in the draft or it comes out.

## Reproducibility is the verification step

A change is finished when someone else can regenerate the number:

- Every result came from `analysis_run` and has a ledger entry.
  `analysis_ledger(action="verify", run_id=…)` must be INTACT before the
  number goes in the paper — DRIFTED means the figure no longer matches its
  inputs.
- Environment pinned (lockfile / renv / Manifest / container digest), seeds
  set and recorded, data files hashed.
- Every figure and table carries the exact command that regenerates it.
- `README` says: clone → restore env → one command → these outputs.
  → `reproducibility.md`

## Bring the owner in

Stop and ask — do not decide alone — for: scope or research-question changes;
anything involving people, animals, identifiable or restricted data (consent,
IRB/IACUC/IBC, DUA, de-identification — `research-ethics.md`); money, paid
compute, or time on a shared instrument; authorship and credit; publishing,
submitting or posting anything; dual-use ground where the ordinary
methodology is fine but the specifics are not; and any point where the honest
answer is that the study cannot decide.

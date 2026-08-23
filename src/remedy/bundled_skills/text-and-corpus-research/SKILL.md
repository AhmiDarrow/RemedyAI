---
name: text-and-corpus-research
description: >
  Use when the data is text and the claim is empirical: building and licensing a
  corpus, sampling frames for documents, cleaning and tokenisation that does not
  destroy the signal, annotation as a measurement instrument with multiple coders
  and agreement, frequency/collocation/keyness, topic models and embeddings,
  LLM-assisted coding validated against human labels, and reproducibility when
  the texts cannot be redistributed.
version: 1.0.0
author: Remedy
tags: [research, corpus, nlp, annotation, digital-humanities, text-analysis]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_profile, stats_assumptions, stats_multiplicity, lit_search, cite_add, manuscript_build, skill_activate, file_read, file_write]
triggers:
  - '\b(corpus (?:linguistics|analysis|building|design)|concordanc\w+|\bKWIC\b|n-?gram frequenc\w+|type[- ]token ratio)\b'
  - '\b(topic model(?:l?ing|s)?|\bLDA\b|tf-?idf|word embeddings?|named entity recognition|\bNER\b)\b'
  - '\b(inter[- ]annotator agreement|annotation (?:scheme|guidelines?)|gold[- ]standard (?:corpus|annotations?)|adjudication round)\b'
  - '\b(OCR (?:quality|errors?|accuracy)|text normali[sz]ation|lemmati[sz]\w+|stopword lists?|tokeni[sz]ation)\b'
---

# Text and corpus research

Run `skill_activate(skill="research-method")` first and work from that spine —
question framing, evidence standards, preregistration, citation honesty, what
verification means. Do not restate it. This pack covers what is different when
the data is text: it was written by someone, licensed by someone, digitised
imperfectly, and every "measurement" is a chain of preprocessing decisions.

## Decision tree

1. **Say what the corpus is a sample of.** A corpus is a sampling frame with a
   population behind it: all UK newspapers 1990-2020, or whatever the API
   returned last Tuesday. Which one it is decides what the finding is about.
   Record inclusion rules, time span, language(s), genre, and what you could not
   get. `references/corpus-construction-and-licensing.md`.
2. **Settle licensing and terms of service before collecting.** Copyright,
   database rights, platform ToS, robots directives, and personal data in the
   text are collection-time decisions, not publication-time regrets. If the texts
   cannot be redistributed, plan from day one to ship the IDs, the extraction
   script and the derived counts instead.
3. **Freeze a raw layer.** Store the bytes as fetched with their encoding,
   checksum and retrieval date. Every cleaning step is a new derived layer with
   its own script. You will need to answer "was that an OCR error or a spelling
   change" a year from now. `references/cleaning-and-tokenisation.md`.
4. **Measure the noise before you measure the signal.** Sample documents by hand
   and estimate OCR/ASR error rate, encoding damage, boilerplate share and
   duplicate rate. A frequency difference between two subcorpora with different
   OCR quality is an OCR finding until you rule it out.
5. **Normalise minimally and reversibly.** Case-folding, lemmatisation, stopword
   removal and aggressive tokenisation each destroy something. Justify each one
   against the research question; report the pipeline in order; never use a
   stopword list you have not printed and read.
6. **If humans label it, that is an instrument.** Written guidelines, at least
   two independent annotators on a real overlap sample, a reported agreement
   coefficient, and an adjudication procedure. One annotator plus "it was
   obvious" is not measurement. `references/annotation-and-agreement.md`.
7. **Pick the analysis for the claim.** Counts and keyness for "what is
   distinctive"; collocation for "what goes with what"; classification for "how
   many documents are X"; topic models and embeddings for exploration and
   hypothesis generation, not as evidence on their own.
   `references/classic-corpus-analyses.md`,
   `references/topic-models-and-embeddings.md`.
8. **LLM-assisted coding is allowed and must be validated.** Treat the model as
   an annotator: same guidelines, held-out human-labelled sample, reported
   agreement, reported prompt and model version, and error analysis by class.
   `references/llm-assisted-coding.md`.

Read `references/INDEX.md` and pull what you need with `file_read`.

## Running it

- `analysis_env(path)` first — spaCy, NLTK, stanza, gensim, scikit-learn,
  transformers, R quanteda/tidytext, AntConc live in the project, not in this
  process. Everything heavy runs through `analysis_run` so argv, input hashes
  and outputs land in the ledger.
- `data_profile` on the document metadata table (ids, dates, source, length,
  language): duplicates, missing dates and a near-duplicate spike are corpus
  bugs that look like results.
- Counts need a denominator. Report normalised frequency (per million words or
  per document) with the corpus sizes beside it, and give an interval — the
  standard binomial interval assumes independent tokens, which is false in text,
  so say the interval is optimistic or use a dispersion-aware measure.
- `stats_multiplicity` whenever you scan a vocabulary: a keyness table over
  50,000 word types is 50,000 tests. Report the adjusted results, and note that
  a word ranked top by a significance statistic is often just frequent.
- `stats_assumptions` before any test on lengths, scores or rates; text
  distributions are heavy-tailed and Zipfian, so normality-based defaults are
  usually the wrong choice.

## Distant reading, honestly

A model's output is a measurement with error, not a reading. "Topic 7 is about
immigration" is a claim about the top terms plus your interpretation; check it
by reading a random sample of high-weight documents and reporting how many fit.
Anything you would not defend after reading twenty documents does not go in the
abstract.

## What counts as verified here

1. **The pipeline reruns from the raw layer** and reproduces the reported counts
   — `analysis_run`, then `analysis_ledger(action="verify", run_id=...)` INTACT.
2. **The measurement has a validation set**: a human-labelled sample the model or
   rule never saw, with agreement/precision/recall per class reported, not just
   overall accuracy.
3. **Preprocessing sensitivity is shown**, not assumed: the headline result
   survives a reasonable alternative tokenisation, stopword and normalisation
   choice, or you report where it does not.
4. **A qualitative spot check exists**: a random sample of documents behind the
   claim was read, and the hit/miss count is in the paper.
5. **Reproducibility package is real** for a non-redistributable corpus: document
   IDs, retrieval script and date, preprocessing code, derived counts, and the
   exact model/prompt versions. `references/evaluation-and-reporting.md`.
6. `cite_check(manuscript, resolve=True)` returns PASS, and corpora, tools and
   pretrained models are cited with versions.

# Evaluation, reproducibility and reporting

## Evaluate against human judgement, and report the agreement

- The held-out set is labelled by humans, sampled at random from the corpus the
  claim is about, and never used for development. Report its size and how it was
  drawn.
- Report per-class precision, recall and F1 with the support for each class, and
  the confusion matrix. A macro average and a micro average answer different
  questions; give both when classes are imbalanced.
- Give an interval. Bootstrap over documents (not tokens) is the simple honest
  option for text.
- Report the human ceiling: agreement between the human annotators on the same
  items. A classifier at 0.78 F1 where humans agree at 0.80 is a different
  result from one where humans agree at 0.98.
- Compare against a real baseline — majority class, a keyword rule, a simple
  bag-of-words classifier. A large model that barely beats a keyword list is a
  finding worth reporting.
- Splits must not leak: near-duplicates, the same author, the same thread or the
  same source across train and test inflate scores. Deduplicate and split by
  document group, and say which grouping was used.
- For temporal claims, evaluate out of period: train on earlier text, test on
  later. In-period cross-validation overstates how the model will behave on the
  years you want to describe.

## Distant reading, stated honestly

Model output is a measurement with error, not a reading. Every aggregate claim
carries its measurement error into the estimate; say so. A random sample of
documents behind the headline claim should be read, and the number that fit
reported. Anything that would not survive reading twenty documents does not
belong in the abstract.

## Reproducibility when the corpus cannot be shared

The package that makes the work checkable without redistributing text:

- **Document IDs** — stable identifiers plus the source and retrieval date.
- **Retrieval and extraction scripts** — with the parameters used.
- **Preprocessing and analysis code** — run through `analysis_run` so argv,
  input hashes and outputs are in the ledger; `analysis_ledger(action="verify")`
  before submission.
- **Derived data the licence permits** — counts, document-term matrices,
  annotations, embeddings, model outputs.
- **Annotation guidelines** and the label distribution.
- **Model and tool versions**, seeds, and for hosted models the run date.
- A short statement of what a reader cannot reproduce and why.

## In the manuscript

Corpus size in documents and tokens per subcorpus, sampling frame, licence
status, cleaning pipeline in order, tokeniser and version, annotation apparatus
and agreement, model and evaluation with intervals, and the multiplicity
handling for any vocabulary-wide scan. Build with `manuscript_build`, fix the
undefined citations it reports, and require `cite_check(resolve=True)` PASS —
corpora, tools and pretrained models cited with versions, like any other source.

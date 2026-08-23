# text-and-corpus-research references

- corpus-construction-and-licensing.md — sampling frame and inclusion rules, balanced vs opportunistic design, deduplication, copyright and terms of service, personal data, provenance and versioning; read before collecting a single document.
- cleaning-and-tokenisation.md — raw layers, encoding and Unicode normalisation, measuring OCR/ASR error, boilerplate, tokenisation across languages, and which normalisation choices destroy the signal; read before any count.
- annotation-and-agreement.md — guidelines and versioning, annotator training and adjudication, choosing between kappa, weighted kappa, Krippendorff alpha and span F1, and what to report; read whenever humans assign labels.
- classic-corpus-analyses.md — normalised frequency and dispersion, keyness and its reference-corpus and multiplicity traps, collocation measures, and reading concordance lines as the check on a count; read for descriptive corpus claims.
- topic-models-and-embeddings.md — topic-model instability and choosing K, human validation of topics, embedding spaces, diachronic alignment, contextual embedding caveats; read before interpreting any unsupervised output.
- llm-assisted-coding.md — the protocol for using a language model as an annotator, development vs validation samples, per-class reporting, error analysis, correcting downstream estimates, and what must be recorded about the model.
- evaluation-and-reporting.md — held-out human judgement, per-class metrics and intervals, the human ceiling, baselines, leakage-free and temporal splits, and the reproducibility package for a corpus that cannot be redistributed.

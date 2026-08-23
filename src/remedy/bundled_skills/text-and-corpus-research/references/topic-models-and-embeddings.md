# Topic models and embeddings

Both are exploratory instruments. They generate hypotheses and summaries; on
their own they are not evidence for a substantive claim.

## Topic models

- **Instability is the defining property.** Different seeds, different numbers
  of topics and small preprocessing changes produce different topic sets. Run
  several seeds and report how stable the interpreted topics are; a topic that
  appears under one seed only does not go in the abstract.
- **K is a choice, not a discovery.** Coherence and perplexity curves help but
  disagree with each other and with human judgement; perplexity in particular
  often prefers models humans find worse. Report how K was chosen and show the
  result under at least one other K.
- **Preprocessing drives the output** more than the algorithm does: stopwords,
  minimum document frequency, n-grams, lemmatisation, very short documents.
  Report the vocabulary size and document length distribution.
- **Validation is human.** Label a topic only after reading a random sample of
  its high-weight documents, and report the proportion that fit the label.
  Top-10 terms alone are not enough to name a topic.
- Structural topic models let covariates affect prevalence or content; the
  covariate effect is then a modelled estimate with uncertainty — report the
  interval, not just the direction.

## Word and document embeddings

- Embeddings encode co-occurrence in the training corpus. A distance is evidence
  about that corpus, not about language in general — say which corpus and which
  model version produced it.
- Off-the-shelf pretrained vectors carry the training data's period, register
  and biases. For a historical or specialist corpus, train in-domain or justify
  the transfer explicitly.
- **Diachronic comparisons need alignment.** Separately trained spaces are not
  comparable without orthogonal Procrustes alignment or an incremental training
  scheme, and even then apparent semantic change can come from frequency shifts
  and sampling. Test against a control set of words expected not to change.
- Nearest neighbours are unstable for low-frequency words and vary across
  training runs. Train multiple runs and report neighbours that persist, with a
  frequency floor.
- Contextual embeddings from transformer models are layer- and
  pooling-dependent; state the model, checkpoint, layer and pooling. Cosine
  similarity from these is anisotropic and inflated — do not read raw values as
  meaningful magnitudes without a baseline.
- Bias measurements from embeddings depend heavily on the word lists and the
  metric; treat a single association score as fragile and print the word lists.

## Reporting either

Model, library and version, hyperparameters, random seeds, preprocessing
pipeline, vocabulary size, and the human validation step with its numbers. Run
training and inference through `analysis_run` so seeds and inputs are in the
ledger; a topic model without a recorded seed is not reproducible.

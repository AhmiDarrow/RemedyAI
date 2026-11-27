# Cleaning, OCR noise and tokenisation

## Layers, not edits

Keep the raw layer immutable. Each cleaning stage is a script producing a new
layer with its own checksum, run through `analysis_run` so the order is
recoverable. You will be asked "was that a spelling variant or an OCR error"
and only the raw layer can answer.

## Encoding

Detect and record the encoding rather than assuming UTF-8. Symptoms of a
mis-decode: mojibake sequences, stray replacement characters, curly quotes
appearing as multi-byte garbage. Normalise Unicode deliberately (NFC is the
usual choice) and be aware that NFKC folds ligatures, superscripts and
full-width forms — sometimes wanted, sometimes destructive for historical or
multilingual material. Record which normalisation form was applied.

## OCR and ASR error

- Estimate the error rate: hand-correct a random sample of pages or minutes and
  compute character/word error rate with a confidence interval. Report it.
- Error rate varies by source, period, font, layout and speaker — a differential
  error rate between subcorpora will masquerade as a linguistic difference.
  Test the comparison on a hand-corrected subsample before believing it.
- Common OCR confusions (long s read as f, rn as m, digit/letter swaps) create
  spurious rare types. Filter or map them with a documented rule, not silently.
- Layout matters: running heads, page numbers, footnotes, column bleed and
  hyphenation across line breaks all enter the token stream. De-hyphenate with
  care — it merges real hyphenated compounds too.

## Boilerplate and structure

Strip navigation, cookie notices, disclaimers and signature blocks before
counting, and report how much was removed. Keep structural markup (paragraph,
heading, quotation, speaker turn) where the question needs it; flattening a
transcript loses turn boundaries you may want later.

## Tokenisation

- The tokeniser is a measurement decision. Whitespace, rule-based, language-
  specific and subword tokenisers give different type counts, so a type-token
  ratio is only comparable within one tokenisation.
- Languages without whitespace word boundaries (Chinese, Japanese, Thai) need a
  segmenter, and segmenters disagree; name the tool and version.
- Rich morphology (Finnish, Turkish, Arabic) makes surface forms sparse;
  lemmatisation or morphological analysis is often necessary, and it introduces
  its own errors — measure them on a sample.
- Clitics, contractions, hashtags, URLs, emoji and code-switching each need an
  explicit rule.

## Normalisation choices to justify

Lowercasing (destroys named-entity signal), stopword removal (print the list and
read it; standard lists remove negation and pronouns that may be the finding),
stemming vs lemmatisation, number and date masking, historical spelling
normalisation (VARD-style mapping tables change what a diachronic study can
see). Report the pipeline in order, and show the headline result under at least
one alternative choice.

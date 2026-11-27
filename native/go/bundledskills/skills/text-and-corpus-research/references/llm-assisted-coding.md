# LLM-assisted coding and classification

A language model used to label documents is an annotator with unusual
properties: fast, cheap, consistent within a prompt, and confidently wrong in
ways a human is not. Use it, and validate it as any human coder is validated.

## Protocol

1. **Write the guidelines first**, for humans, as in `annotation-and-agreement.md`.
   The prompt is a rendering of the guidelines, not a substitute for them.
2. **Human-label a validation sample** drawn at random from the corpus, by at
   least two annotators, with agreement reported. This is the reference the
   model is measured against, and it must be labelled before the model output is
   seen.
3. **Develop the prompt on a separate development sample.** Iterating the prompt
   against the validation set turns it into training data and inflates the
   reported performance.
4. **Run the model on the full corpus** with fixed settings, then report
   agreement with the human labels on the untouched validation set: per-class
   precision, recall and F1, plus the same agreement statistic used for the
   human pair. Reporting overall accuracy alone hides failure on the rare class
   that is usually the point of the study.
5. **Error analysis by class**: read the disagreements. Systematic errors
   (sarcasm, negation, a domain sense, documents truncated by the context
   window) bias estimates in a direction that can be corrected for; random error
   mostly attenuates.
6. **Correct downstream estimates** where the design allows: a prevalence
   estimated from imperfect classifications is biased, and design-based
   correction using measured error rates on the validation sample gives an
   interval that includes classification error. Say whether you did this.

## What must be recorded

Model name and exact version or checkpoint, provider, date of the run,
temperature and sampling settings, the full prompt including any system message,
output parsing rules, retry and refusal handling, and how many items failed to
parse. Hosted models change under a stable name — the run date and the version
string are part of the method, and an exact rerun may be impossible. Say so
plainly rather than implying reproducibility you cannot deliver.

## Cautions

- Do not use the same model family to generate and to evaluate labels and call
  that validation; report human agreement.
- Self-reported confidence scores are not calibrated probabilities unless
  calibration was measured on the validation set.
- Option order and formatting shift model outputs; randomise option order where
  labels are exchangeable and check sensitivity.
- Sending text to a hosted API is a data transfer. Confirm the licence, the
  ethics approval and any data-protection rule permit it before the first call;
  for personal or restricted data, use a locally run model or do not do it.
- Never let the model invent a source, a quotation or a document id. Anything it
  attributes must be checked against the corpus.

# Annotation as a measurement instrument

Labels produced by people are measurements with error. The annotation apparatus
is reported with the same care as an instrument in any other field.

## Guidelines

Write them before annotating and version them. Each category needs: a
definition, inclusion and exclusion criteria, at least one clear example, one
borderline example, and the decision rule for the borderline. Add a decision
order for overlapping categories, and a rule for "cannot tell" — forcing a
choice hides uncertainty rather than removing it.

Guidelines change during a pilot; that is normal. When they change, re-annotate
the affected material or record which version produced which labels.

## Annotators and process

- At least two annotators independently on a real overlap sample — enough to
  estimate agreement with a usable interval, and drawn at random from the whole
  corpus, not from the easy part.
- Train on a pilot batch, discuss disagreements, revise guidelines, then start
  the measured round. Agreement from the training batch is not the reported
  figure.
- Record annotator identity per label so annotator effects can be modelled.
- Adjudication: a documented procedure (third annotator, discussion to
  consensus, or adjudicator decides). Say which, and report how many items went
  to adjudication.
- Crowdsourced labels need the same apparatus plus quality controls, and fair
  pay is part of the ethics of the study.

## Agreement statistics

- **Percentage agreement** alone is not reportable — it ignores chance.
- **Cohen's kappa**: two annotators, nominal categories, same items.
- **Weighted kappa**: ordinal categories where near-misses matter.
- **Krippendorff's alpha**: any number of annotators, missing data, and nominal
  to ratio measurement levels. The general-purpose default.
- **Fleiss kappa**: multiple annotators, fixed number per item.
- **F1 against a reference** is used for span and entity tasks where the unit
  boundary is itself annotated; agreement must then be defined over spans
  (exact vs partial match) and the criterion stated.

Kappa-family statistics are depressed by skewed category distributions — a rare
class can give low kappa at high accuracy. Always report the confusion matrix
and per-class agreement, not a single number. Thresholds like 0.67 or 0.80 are
repeated conventions from particular papers, not laws; if you cite one, cite the
source you actually read, and say what the disagreement pattern was.

## What goes in the paper

Guidelines (as a supplement), number of annotators and their relevant
background, overlap size and how sampled, agreement statistic with the software
used, confusion matrix, adjudication rule, and the final label distribution. If
agreement is low, that is a finding about the construct's definability — report
it rather than tuning the guidelines until the number looks good.

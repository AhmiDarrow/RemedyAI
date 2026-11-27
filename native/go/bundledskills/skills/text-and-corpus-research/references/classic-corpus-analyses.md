# Frequency, collocation, keyness, concordance

## Frequency needs a denominator and a dispersion

Raw counts are uninterpretable across corpora of different sizes. Report
normalised frequency (per million words, or per document) with corpus sizes
beside it. Then report **dispersion**: a word occurring 500 times in one
document is not a corpus-level pattern. Standard dispersion measures (Juilland's
D, Gries's DP and relatives) exist; name the one used and its direction, and
give the number of documents the word appears in as a plain, checkable figure.

Intervals on frequencies from the binomial assume independent tokens, which is
false — words cluster within documents. Treat such intervals as optimistic, or
compute uncertainty across documents (bootstrap over documents, not tokens).

## Keyness

Keyness compares a target corpus against a reference corpus to find distinctive
words. Choices that decide the answer:

- **The reference corpus.** Keyness is a statement about a contrast, not about
  the target alone. Say what the reference is and why it is comparable.
- **The statistic.** Log-likelihood and chi-square rank by evidence and so favour
  frequent words; effect-size measures (log ratio, %DIFF, odds ratio) rank by
  size and so favour rare ones. Report both a significance and an effect-size
  column, sorted by effect size with a minimum-frequency filter.
- **Multiplicity.** A keyness table over a 50,000-type vocabulary is 50,000
  tests. Run `stats_multiplicity` and report adjusted results.
- Content words dominate unless function words and lemmas are handled
  deliberately; state the pre-processing.

## Collocation

Collocation asks which words co-occur more than chance within a window. Specify:
window size and whether it is directional, whether it crosses sentence
boundaries, the frequency threshold, and the association measure. Mutual
information favours rare pairs, log-likelihood and t-score favour frequent ones,
log-Dice is more stable across corpus sizes. Different measures produce
different top-20 lists from the same data — show more than one, or justify the
choice against the research question.

## Concordance and KWIC

Reading concordance lines is not an optional extra; it is the check that a count
means what you think. For any word or pattern in a headline claim, pull a random
sample of concordance lines, classify the senses or functions by hand, and
report the proportion that supports the interpretation. This catches homonymy,
irony, negation, quotation and metadata leaking into text — none of which a
frequency table shows.

## n-grams and lexical measures

n-gram counts inherit every tokenisation decision and are sparse: report the
minimum frequency threshold and how many types were discarded. Type-token ratio
depends on text length, so use a standardised or moving-average variant and give
the window. Readability formulae are calibrated on particular genres and
languages; name the formula and do not treat a grade level as a fact.

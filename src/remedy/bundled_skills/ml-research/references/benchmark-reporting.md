# Benchmark reporting

## The table must support the claim

Every results table carries, per row: method, the split it was evaluated
on, the tuning budget, the number of seeds, mean and spread, and the
metric implementation. A table of bare numbers is not evidence.

The delta to the strongest baseline gets an interval and a paired test
(see variance-and-seeds), reported in the metric's own units. Do not bold
a difference that lies inside the seed spread, and do not describe a
0.2-point move as "substantially outperforms".

## Fair comparison to prior work

A comparison is like-for-like only when all of these match: dataset
version, split, preprocessing, metric implementation, tuning budget, and
what counts as one "run". Any mismatch goes in the caption.

- Re-run the competitor whenever the code exists; cite the paper for the
  configuration and record what you had to choose yourself.
- When re-running is impossible, mark the row **"reported by [cite]"**,
  state which conditions differ, and never present it as a controlled
  comparison. Add the citation with `cite_add` and let `cite_check`
  resolve it before the manuscript is called done.
- Metric implementations disagree (tokenisation, averaging, tie
  handling). Name the package and version.
- If the benchmark has a hidden test server, report the dev result too.

## Contamination

For any model pretrained on web-scale data, ask whether the benchmark is
in the training corpus. Do the check you can — n-gram overlap between
test items and any accessible training data, canary strings, performance
on a freshly collected variant — and report both the result and the
limits of the check. Where the pretraining data is not inspectable, say
exactly that instead of implying the benchmark is clean.

## What else goes in the paper

- **Compute and cost**: hardware, total GPU/CPU hours for training, for
  the search, and for evaluation. This is what makes a replication
  budgetable.
- **Failure analysis**: where the model is wrong, on which slices, with
  examples. Usually the most reused part of the paper.
- **Negative results** from the same project, at the same detail.
- **Limitations** naming the population the result covers and the
  conditions under which it was not tested.
- **Model card** (intended use, out-of-scope use, training data summary,
  evaluation slices, limitations) and a **datasheet** for any released
  dataset. Name the framework you followed and cite it; fetch the current
  version rather than paraphrasing its section list from memory.
- **Licences and provenance** of every dataset and pretrained checkpoint,
  and whether the licence permits your use. Where the data concerns
  people, state the consent basis and the de-identification applied —
  that belongs in the method, not a footnote.

## Leaderboards

A leaderboard rank is not an effect size. Report the score, the gap to
the next entry, and whether that gap exceeds your seed spread. Repeated
submissions against a hidden test set are optional stopping against that
set; report how many you made.

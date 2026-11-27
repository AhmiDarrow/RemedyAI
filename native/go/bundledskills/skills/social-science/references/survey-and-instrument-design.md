# Survey and instrument design

## Prefer an existing validated instrument

Search first (`lit_search`) for a published scale measuring the construct, and
record: the instrument name, version, the population it was validated in, its
licence or permission terms, and its scoring rules. Using a validated scale and
then dropping items, changing the anchors or shortening it means it is no longer
that scale — report it as an adaptation and re-examine reliability.

## Item wording

- One idea per item. "The service was fast and friendly" is two questions.
- Avoid negations, especially double negations; reverse-coded items catch
  straightlining but also catch confusion, and they load on a method factor.
- Match the respondent's vocabulary, not the literature's; jargon becomes
  guessing.
- Reference period must be explicit ("in the last 7 days"), and short enough to
  be recalled.
- Sensitive items: give a non-judgemental preamble, offer "prefer not to say",
  and consider list experiments or randomised response when social desirability
  is the dominant threat.
- Leading and loaded wording changes answers materially; test alternatives in a
  split-ballot if the wording is contested.

## Response scales

- Decide and justify: number of points, whether a midpoint exists, whether a
  "don't know" is offered, and whether the scale is fully labelled. Each choice
  changes the distribution.
- Keep polarity and direction consistent within a block; if you must switch,
  signal it visually.
- Likert items are ordinal. Summed multi-item scales are commonly treated as
  interval — that is a convention with limits; check that it does not drive the
  result, or use ordinal models.

## Order and context effects

Earlier items prime later ones (assimilation and contrast). Randomise item
order within blocks and response-option order where meaning allows, and record
the realised order per respondent so it can be tested as a covariate. Keep
demographics late unless they are screeners; keep the outcome away from an
adjacent item that defines it.

## Data quality controls

Attention checks (instructed-response items), plausible-timing thresholds,
straightlining detection, duplicate IP/device flags, and open-text gibberish
screening. Decide exclusion rules **before** looking at the outcome and
preregister them; report how many were excluded by each rule and rerun the main
analysis without exclusions as a robustness check.

## Pilot before fielding

Cognitive interviews or think-alouds with a handful of target respondents catch
more problems than any statistic. Then a soft launch: field to a small slice,
check completion time, drop-off point, missingness per item and floor/ceiling
effects before the full release.

## Reporting

Ship the full instrument as an appendix or supplement, with routing logic,
randomisation, and the exact wording in the language(s) used. A translated
instrument needs forward/back translation and a note on equivalence.

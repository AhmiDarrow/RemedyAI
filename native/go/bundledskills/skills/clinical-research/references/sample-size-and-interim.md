# Sample size, interim analyses, stopping

## Sizing the study

`power_analysis(test=..., solve="n", alpha=..., power=..., effect_size=...,
dropout=..., clusters=..., icc=...)`.

Inputs that must be argued, not guessed:

- **The effect.** Use the smallest difference that would change practice
  (the minimal important difference), not the effect seen in a small pilot —
  pilot effects are inflated by the winner's curse. State where the number
  came from and cite it (`cite_add`). If nobody can name one, that is the
  conversation to have before the calculation.
- **The variance / event rate**, with its source and vintage. Control-arm
  event rates drift downward over time; an old rate oversizes the effect and
  undersizes the trial.
- **alpha and sidedness.** Two-sided unless there is a real argument;
  one-sided alpha of 0.025 is the usual translation.
- **Attrition.** Inflate n by 1/(1 - dropout). Clusters add the design
  effect 1 + (m-1)*ICC.
- **Multiplicity.** Co-primary endpoints require all to succeed (power
  multiplies down); multiple comparisons need the adjusted alpha in the
  calculation, not after.

Report the sensitivity table `power_analysis` returns: the study is powered
for an assumption, and if the true effect is smaller it is underpowered.
Post hoc "observed power" computed from the result is a restatement of the
p-value and is not informative — do not report it.

## Non-inferiority margins

The margin is clinical, justified against the historical benefit of the
active control, fixed before data, and stated in the registry. Sample size
for non-inferiority is usually larger than for superiority at the same
alpha. Both ITT and per-protocol are reported.

## Interim analyses

- Every unblinded look at accumulating outcome data spends type-I error.
  Pre-specify the number and timing of looks and the spending function
  (O'Brien-Fleming style boundaries are conservative early; Pocock spends
  evenly). Haybittle-Peto boundaries keep the final alpha nearly intact.
- **Futility** boundaries (conditional power below a stated threshold) may
  be non-binding, but say which.
- **Sample size re-estimation** on the blinded nuisance parameter (pooled
  variance or overall event rate) is generally safe; unblinded re-estimation
  needs a method that protects alpha and must be pre-specified.
- Estimates at an early stop are biased away from the null. Report the
  adjusted estimate where a method exists, and say the direction of the bias
  where it does not.

## DSMB and stopping

An independent data monitoring committee sees unblinded data on a charter
agreed in advance: membership, frequency, boundaries, and who may be told
what. The investigators do not look. Stopping rules cover efficacy, harm and
futility; harm can stop a trial without a boundary being crossed. Every
interim look, its date, and its decision go into the final report — a look
that is not reported makes the final p-value unverifiable.

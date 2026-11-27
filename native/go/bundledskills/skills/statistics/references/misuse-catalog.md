# Misuse catalog

Recognise these in the owner's plan, in a draft, and in a paper under
review. Name the problem and offer the fix.

**HARKing** — hypothesising after the results are known, then presenting
it as a priori. Fix: keep exploratory findings labelled exploratory, and
state that confirmation needs new data. A pre-registration timestamp is
the only thing that distinguishes the two.

**Optional stopping** — peeking and stopping when p crosses 0.05. Inflates
the false-positive rate substantially (it approaches 1 with unlimited
looks). Fix: fix n in advance, or use a group-sequential design with
alpha spending, or a Bayesian design with a pre-stated rule.

**Garden of forking paths** — no explicit multiple testing, but many
defensible analysis choices (exclusions, transforms, covariates,
subgroups) made after seeing data. Fix: pre-specify, or run a
multiverse/specification-curve analysis across all reasonable choices.

**p-hacking by covariate** — adding or dropping controls until the
coefficient moves. Fix: one pre-specified adjustment set from the DAG;
report unadjusted and adjusted side by side.

**Dichotomania** — median-splitting a continuous variable. Loses power,
manufactures a threshold, and can reverse a sign. Fix: keep it continuous
and model non-linearity with splines if needed.

**Simpson's paradox** — an association that reverses within every
subgroup. Fix: it is a causal question, not a statistical one. The DAG
says whether to condition on the grouping variable (confounder: yes;
mediator or collider: no). Always plot the subgroups.

**Regression to the mean** — extreme groups selected on a noisy baseline
move toward the mean on remeasurement, with no treatment at all. Fix: a
control group, or change scores modelled with baseline adjustment
(ANCOVA), never "improvement in the worst group" as evidence.

**Base-rate neglect** — reading a significant test as high probability
the hypothesis is true. With a low prior and typical power, most
significant results in a broad screen are false. Fix: report FDR.

**Confusing the p-value's meaning** — it is P(data at least this extreme |
null true), not P(null true), not P(replication), not effect size.

**"Difference between significant and non-significant"** — comparing two
tests instead of testing the interaction. Fix: fit the interaction.

**Overinterpreting non-significance** — absence of evidence. Fix: report
the CI, or run an equivalence test (TOST) against a pre-stated bound.

**Pseudo-replication** — technical replicates, repeated measures, or
littermates counted as independent n. Inflates df and shrinks p. Fix:
n is the number of independent units the treatment was applied to.

**Selective outcome reporting** — measuring six outcomes, reporting one.
Fix: report all pre-specified outcomes; a registry entry makes this
checkable.

**Post-hoc power** — computed from the observed effect, it is a
transformation of the p-value and tells you nothing new. Fix: report the
CI, or the minimum detectable effect at the achieved n.

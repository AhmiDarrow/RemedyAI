# Hyperparameter search

## Budget is part of the claim

Every comparison is a comparison at a budget. Define it up front — number
of trials, epochs per trial, wall clock, or GPU-hours — and give **every**
method in the table the same one, including the baselines and the
ablations. A method that wins only because you searched its space 10x
harder has not won.

Record for each arm: search space, search algorithm, number of trials,
what was selected on (which validation split, which metric), and the
winning configuration in full.

## Strategy

- **Random search over ranges beats grid search** for the same budget,
  because most hyperparameters do not matter and grid wastes trials
  re-testing the ones that do not. Sample log-uniformly for learning
  rates, weight decay and regularisation strengths.
- **Bayesian optimisation / TPE** helps at larger budgets and with
  expensive trials; report the library and its version, because the
  defaults differ.
- **Successive halving / Hyperband** when trials can be cut early — good
  budget efficiency, but it biases toward fast-starting configurations;
  note that if late-blooming settings matter.
- **Tune the baseline first**, or at least simultaneously. The strongest
  bias in published tables comes from an author who knows their own
  method's good region and not the competitor's.
- Freeze the search space before you start. Widening it after a
  disappointing round is a forking path; if you do widen it, report both
  rounds and the total trial count.

## Selection and reporting

- Select on validation, always. With small validation sets, select on
  cross-validated mean rather than a single fold — otherwise you are
  fitting the validation noise.
- The validation score of the selected configuration is **optimistically
  biased** (it is a maximum over trials). Never report it as the
  performance estimate; report test performance, or use nested CV.
- Report the **distribution over trials**, not just the winner: a method
  whose median trial is decent is more useful than one where 1 in 50
  configurations works.
- Report sensitivity: which hyperparameters actually moved the metric,
  and over what range the method is stable. That is the practical
  contribution readers reuse.
- Re-run the selected configuration with fresh seeds before reporting.
  The winning trial's seed is selected-on and its score is inflated.

## Mechanics

Run sweeps through `analysis_run` so each trial lands in the ledger with
its argv, config hash, input hashes and duration; use `tag=` to group a
sweep and `analysis_ledger(action="list", query=<tag>)` to pull it back.
Write the config to a file per trial rather than encoding it in flags —
the file is what gets hashed and shipped.

Cost honesty: report the total compute the search consumed (trials x
epochs x hardware), not only the final model's training cost. A method
that needs a 1000-trial search to beat a default-configured baseline
should say so.

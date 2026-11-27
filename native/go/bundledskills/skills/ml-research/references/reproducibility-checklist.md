# Reproducibility checklist

Work through this before calling a result done. Anything unchecked is
stated as unchecked in the write-up.

## Data

- [ ] Raw data location and version recorded, with a hash per file
      (`analysis_run` records input hashes automatically).
- [ ] Provenance and licence of every dataset; consent basis and
      de-identification where the data concerns people.
- [ ] Split code and split seed committed; split sets regenerable and
      their id sets verified disjoint.
- [ ] `data_profile` output stored, leakage suspects resolved in writing.
- [ ] Preprocessing is code, not manual edits; it runs from raw to
      model-ready in one command.

## Environment

- [ ] `analysis_env(path)` output stored: interpreter, framework and CUDA
      versions, what was found on PATH.
- [ ] Exact dependency versions pinned (lockfile, `pip freeze`,
      `renv.lock`, container digest). "requirements.txt with ranges" is
      not a pin.
- [ ] Hardware recorded: GPU model and count, CPU, RAM, and whether
      mixed precision was on.

## Runs

- [ ] Every reported number produced by an `analysis_run` invocation with
      a run_id; no numbers typed by hand.
- [ ] All RNG seeds set and recorded (framework, numpy, python,
      dataloader workers); determinism flags recorded.
- [ ] Number of seeds ≥ 5 (or the deviation stated), spread reported.
- [ ] Config files, not just flags, saved per run.
- [ ] Checkpoint selection rule stated and applied on validation only.
- [ ] `analysis_ledger(action="verify", run_id=...)` returns `INTACT` for
      every run behind a reported number.
- [ ] Negative controls (shuffled labels, ablated input) run and stored.

## Evaluation

- [ ] Metric implementation named and versioned.
- [ ] Test set touched once; the count of test evaluations is honest.
- [ ] Uncertainty reported from both test-set bootstrap and seed spread.
- [ ] Subgroup/slice results reported.
- [ ] Baselines re-run by you at the same budget, or explicitly labelled
      as quoted.

## Release

- [ ] Code released with the exact commit that produced the tables, and a
      README giving the one command per table/figure.
- [ ] Small artifacts (configs, metrics json, logs) versioned; large ones
      (checkpoints, datasets) deposited somewhere citable with the
      identifier written into the paper.
- [ ] Model card and datasheet where a model or dataset is released.
- [ ] Compute cost of the full study reported.
- [ ] A clean-checkout rerun was attempted on at least one headline
      number, and its outcome recorded — matched, matched within seed
      spread, or did not match.

## The last question

Could someone with the repo, the data and the paper reproduce Table 1
without asking you anything? If the answer needs a caveat, write the
caveat into the paper.

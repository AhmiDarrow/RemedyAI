# Systematics and blind analysis

## Statistical versus systematic

Statistical uncertainty shrinks as 1/sqrt(N); systematic uncertainty does
not. That is the operational test: if ten times the data would not help, it
is systematic. Quote them separately — a combined number hides whether more
running time buys anything.

## How systematics are actually estimated

There is no formula. Each budget row names a **method**:

1. **Vary the choice and re-run.** Change the cut, binning, fit range,
   background model or calibration version; take the shift. Say whether you
   take the shift, half the spread, or an RMS over variations — they differ.
2. **Propagate a calibrated input.** A gain, efficiency or beam energy with
   its own uncertainty, pushed through.
3. **Control samples.** Measure where the answer is known; the residual bias
   is the uncertainty.
4. **Toy Monte Carlo.** Inject a known truth, run the chain, measure the bias
   and its spread.
5. **Data/MC comparison.** Where simulation is used to correct, the
   uncertainty is how well it reproduces a control distribution.
6. **A bound.** When nothing better exists, an argued upper bound with the
   reasoning written out is legitimate; an unargued round number is not.

Table rules: one row per effect with method, size, and whether it correlates
with other rows. Do not double-count an effect appearing twice, and do not
add a systematic "for safety" silently. Quadrature only for uncorrelated
rows.

## Blind analysis

Blinding hides the answer while the choices are made:

- **Offset blinding**: add an unknown constant or scale to the final
  observable; diagnostics still work.
- **Region masking**: the signal window is not looked at until selection is
  frozen.
- **Salting / scrambling**: shuffle labels or times so a real signal cannot
  form.
- **Cell blinding**: analysts see only a fraction or a permuted subset.

Protocol: write down selection, calibration, fit procedure and unblinding
criteria **first**; cross-check on control samples; unblind once. Anything
changed afterwards is reported as a post-unblinding change, with what changed
and why. Re-blinding after a surprise is not a fresh blind.

## Significance conventions

The five-sigma convention in particle physics absorbs the trials factor and
unquantified systematics; it is a field convention, not a universal truth,
and other fields set or avoid thresholds differently. Say which you apply.

**Look-elsewhere effect**: a local p-value at the best-fit mass or frequency
overstates significance because the search ran over a range. Report local and
global significance, and say how the trials factor was obtained (toys, or an
estimate from the number of independent resolution elements).

## Reporting

State the blinding scheme, the unblinding date, every post-unblinding change,
and the budget table. Confirm the run is in the ledger
(`analysis_ledger(action="show", run_id=...)`) so each number traces back to
the run that produced it.

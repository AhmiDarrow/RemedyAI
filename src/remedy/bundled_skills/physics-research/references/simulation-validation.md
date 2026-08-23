# Validating a simulation

A simulation result is a hypothesis about your code until it reproduces
something independently known. Establish that first; only then use it as
evidence.

## The validation ladder

1. **Analytic limits.** Reduce parameters until a closed form exists — free
   particle, harmonic potential, small angle, ideal gas, linear response,
   zero coupling. Compare numerically and report the relative error.
2. **Conservation laws.** Energy, momentum, angular momentum, charge, mass,
   unitarity, detailed balance. Track drift over the whole run, not just at
   the end; a symplectic integrator bounds energy error, a non-symplectic one
   drifts secularly and that drift is your accuracy budget.
3. **Symmetries.** Rotate, translate, time-reverse, swap identical particles;
   the answer must be invariant to the level of the discretisation.
4. **Convergence.** Halve the timestep, the grid spacing, the basis size, the
   sample count; fit the observed order of convergence and check it against
   the scheme's nominal order. A scheme converging at the wrong order has a
   bug or a limiting error source (boundary treatment, precision).
5. **Published benchmarks.** Reproduce a standard test problem, and cite it
   with `cite_add` after resolving it — never from memory.
6. **Independent implementation.** Different code, different method, same
   answer, is the strongest evidence short of experiment.

Validation against experiment is a separate claim from verification that the
code solves the equations correctly. Keep them distinct in the write-up.

## Statistical simulations

- Seed every generator explicitly; record the seed and the generator name in
  the run. "Random" without a recorded seed is unreproducible.
- For parallel runs, use a counter-based generator or independent streams;
  seeding each rank with rank index alone can correlate streams.
- Monte-Carlo error falls as 1/sqrt(N). Quote it. If the quoted precision
  implies more samples than you ran, the number is wrong.
- Importance sampling and reweighting change the effective sample size;
  report ESS, not N.
- For MCMC report the integrated autocorrelation time, the effective sample
  size, and a convergence diagnostic across chains.

## Detector and instrument Monte Carlo

Geant4-class simulations model geometry, physics lists and digitisation. The
physics list is a choice with an uncertainty attached — vary it and take the
spread as a systematic. Validate acceptance and resolution against a
calibration source or control sample before trusting an efficiency, and keep
simulation statistics well above data statistics.

## Running it here

Simulations run in the project's own environment through `analysis_run`,
never inside Remedy's process — the sidecar has no numeric stack.
`analysis_env(probe=True)` first, to see what the project has. Artifacts and
input hashes land in the ledger, and `analysis_ledger(action="verify")` later
proves a figure still corresponds to its inputs.

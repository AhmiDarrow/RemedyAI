# Verification and validation

Two different questions, and merging them is how a wrong code gets
"validated" by a compensating error in the model.

- **Verification** — *are we solving the equations right?* A mathematics
  question, answered without any experimental data.
  - *Code verification*: the implementation converges at the design order
    to the exact solution of the equations.
  - *Solution verification*: for this run, how big is the discretisation
    error (see convergence-studies.md).
- **Validation** — *are we solving the right equations?* Comparison with
  measurement, including the experiment's own uncertainty.

Verification comes first. Never validate an unverified code.

## Code verification tools

**Method of manufactured solutions (MMS).** The strongest and most
general. Choose a smooth analytic function u*, substitute it into the
governing equations, and take what is left over as a source term. Add
that source to the code, use u* for boundary and initial conditions, and
the code must reproduce u* to the design order under refinement. Pick a
u* with non-trivial derivatives in every direction and every term you
want exercised (a linear u* will not test a second-derivative term); do
not let u* satisfy the natural boundary conditions by accident; verify
the source term itself with a symbolic tool.

**Analytic and limiting solutions.** Free-stream preservation, uniform
flow, plane waves, a Riemann problem, the linear or small-amplitude
limit, a known eigenvalue, a steady state. Cheap — run them in CI.

**Invariants.** Mass, momentum, energy, charge, probability, entropy
direction. Conserved quantities should hold to round-off (or to the
scheme's stated dissipation) over long runs. A slow drift is a bug or a
non-conservative discretisation; report which.

**Symmetry and reversibility.** A symmetric problem must give a symmetric
answer; a time-reversible scheme run backwards should return to the
initial condition to round-off.

An **order-of-accuracy test** on the above is what makes them
verification rather than a smoke test: a single run matching to 3 digits
proves less than a refinement sequence hitting the design slope.

Wire these as tests that run on every change through `analysis_run` and
keep the run ids. A verification suite that has not run since last month
is not evidence about today's code.

## Validation

- Compare against measurement **with uncertainty on both sides**:
  experimental (instrument, repeats, setup) and simulation
  (discretisation from the refinement study, parameter uncertainty,
  model-form uncertainty).
- State the validation metric explicitly and in units, not "good
  agreement". Overlapping error bars are a claim; quantify it.
- Calibrating free parameters against the same data you then validate
  against is circular. Hold out validation cases and say which data were
  used for calibration.
- Validation is only valid for the regime the data covered.
  Extrapolation beyond it is stated as extrapolation.

## Reporting

Say which verification evidence exists (MMS? invariants? observed
order?), what the estimated discretisation uncertainty is, and what was
validated against what. Where a step was not done, write that it was not
done rather than leaving the reader to assume.

# Units and dimensional analysis

## The check

Reduce each term to the seven SI base dimensions: mass M, length L, time T,
electric current I, thermodynamic temperature (theta), amount of substance N,
luminous intensity J. Rules:

- Every term in a sum must have identical dimensions.
- Arguments of exp, log, sin, cos, tanh and of any power series must be
  dimensionless. If one is not, you have an error or a missing scale.
- The left and right sides of the final expression must match, and must match
  the quantity the owner asked for.
- Dimensionless does not mean unitless: rad, sr, dB and ppm carry meaning
  that must survive into the plot label.

Then check a limit you already know (small-angle, non-relativistic,
high-temperature, zero-coupling). Dimensional analysis cannot find a missing
2, pi or factor of 1/2; a limiting case usually can.

## SI, and what changed in 2019

The SI is defined by fixing exact numerical values for c, h, e, k_B, N_A,
the caesium hyperfine frequency and K_cd. Consequences you rely on:

- c, h, e, k_B, N_A have **zero** uncertainty by definition. Do not quote one.
- The kilogram is realised through h, so mass measurements inherit a
  realisation uncertainty, not a prototype drift.
- G, particle masses, the fine-structure constant and most cross-sections are
  measured and carry standard uncertainties that must propagate.

Get numbers from the NIST Fundamental Physical Constants dataset (the CODATA
adjustment) rather than memory, and record the adjustment year alongside the
result. To verify a constant you already have: `lit_fetch` or `web_fetch` the
NIST page for that constant and compare digit for digit; if you cannot fetch
it, say the value is unverified rather than asserting it.

## Unit-aware computation

Remedy ships without pint, astropy.units or sympy.physics.units. Do not
import them here. Instead:

- Do the algebra symbolically in the write-up and record the dimensional
  check as text next to the equation.
- When the project has its own environment, run the unit-aware library
  **there** through `analysis_run` (a small `units_check.py` that asserts each
  expression and prints PASS/FAIL), so the check lands in the ledger as a
  reproducible step rather than as a claim.
- `analysis_env(probe=True)` tells you what the project actually has before
  you write a script that imports it.

## Practical conventions

- Carry units in variable names or in a comment on every constant in code:
  `g_m_s2 = 9.80665` beats `g = 9.81` with no note. 9.80665 m/s^2 is the
  defined standard gravity, not a local measurement — say which you mean.
- Natural units (hbar = c = 1) are fine inside a calculation, but restore SI
  before any number reaches the owner, and state the conversion.
- Angles: state degrees or radians everywhere.
- Prefix errors (m vs milli, u vs micro, k vs K) are a leading cause of
  order-of-magnitude mistakes. When a result is off by a round power of ten,
  grep for bare prefixes first.

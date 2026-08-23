# Floating point

## What binary64 gives you

About 15-17 significant decimal digits; machine epsilon ~2.2e-16. Every
operation rounds, so the result of a long computation is the exact result
of a slightly perturbed problem. binary32 gives ~7 digits — fine for many
fields, fatal for others; say which you used.

`0.1 + 0.2 != 0.3`. Compare with a tolerance chosen for the magnitudes
involved: `abs(a-b) <= atol + rtol*max(abs(a),abs(b))`. Absolute
tolerance alone fails for large values; relative alone fails near zero.
Special values propagate: `NaN != NaN`, and a single NaN can silently
poison a whole field. Add a NaN/Inf check after each major stage rather
than discovering it in the plot.

## Catastrophic cancellation

Subtracting nearly equal numbers destroys the leading digits and
promotes earlier rounding error to the front. Classic cases and fixes:

- Quadratic roots: `(-b + sqrt(b*b-4ac))/(2a)` loses everything when
  `b*b >> 4ac`. Compute the well-conditioned root, then use
  `x1*x2 = c/a`.
- Variance: the "sum of squares minus square of sum" formula cancels.
  Use Welford's online algorithm or a two-pass mean.
- `exp(x)-1` and `log(1+x)` near zero: use `expm1`, `log1p`.
- Differences of close angles/positions: reformulate algebraically, or
  work in a shifted local frame.
- Numerical derivatives: `(f(x+h)-f(x))/h` trades truncation against
  cancellation; the optimal `h` for a first-order difference is around
  `sqrt(eps)*scale`. Complex-step differentiation or automatic
  differentiation avoids the trade entirely — prefer them.

## Summation

Naive summation of n terms accumulates error growing like n*eps. Use
pairwise (what most library `sum` routines do) or Kahan/Neumaier
compensated summation when the sum is long or the terms vary in
magnitude. Summation order matters and differs between serial and
parallel reductions — that alone explains most "the parallel run gives a
different answer" reports.

## Conditioning vs stability

Two different things, and confusing them wastes days:

- **Conditioning** is a property of the *problem*: how much the answer
  moves when the input moves. Condition number kappa means you can lose
  up to log10(kappa) digits no matter what algorithm you use.
- **Stability** is a property of the *algorithm*: a backward-stable
  algorithm returns the exact answer to a nearby problem, so it adds no
  error beyond what the conditioning forces.

Estimate the condition number before blaming the solver. A residual
`||Ax-b||` that is tiny while the answer is wrong is the signature of an
ill-conditioned problem, not a buggy solve.

## Reporting

State the precision used, the tolerances, and — when a quantity is a
difference of large numbers or the condition number is high — the number
of digits you believe. Do not print 12 digits of a result good to 4.
Repeat a headline computation in higher precision (or with a compensated
algorithm) and report the agreement; that is a cheap, real check.

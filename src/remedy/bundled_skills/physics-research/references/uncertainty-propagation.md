# Uncertainty: types, propagation, coverage

The vocabulary follows the Guide to the Expression of Uncertainty in
Measurement (GUM), issued by the JCGM. Check the current edition and its
supplements on the BIPM site before citing clause numbers.

## Type A and Type B

- **Type A**: evaluated by statistical analysis of repeated observations.
  The standard uncertainty of a mean of n readings is s / sqrt(n), with n-1
  degrees of freedom. Report n.
- **Type B**: everything else — calibration certificates, datasheet
  tolerances, digitiser resolution, a published constant, an expert bound.
  Convert the stated bound to a standard uncertainty by assuming a
  distribution and **saying which**: a half-width a with a rectangular
  distribution gives a/sqrt(3); a triangular one gives a/sqrt(6); a stated
  "95 % confidence" from a certificate is usually k=2, so divide by 2.

Neither type is "the random one" or "the systematic one". A systematic effect
can be evaluated Type A, and a random one Type B. Keep the two
classifications separate in your table.

## Propagation

For y = f(x1..xn) with standard uncertainties u(xi) and covariances u(xi,xj):

    u_c(y)^2 = sum_i (df/dxi)^2 u(xi)^2
             + 2 * sum_{i<j} (df/dxi)(df/dxj) u(xi,xj)

Drop the second term only when you have shown the inputs are independent —
shared calibrations, a common probe and repeated use of the same constant all
correlate inputs, and ignoring that usually **understates** the result.

Linearisation fails when f is strongly nonlinear over the range of u, or when
u is a large fraction of x (ratios with a small denominator are the classic
case). Then propagate by Monte Carlo: sample the inputs from their assigned
distributions, push each sample through the analysis, and take the standard
deviation and coverage interval of the output. State samples and seed.

## Coverage

Report either:

- the combined standard uncertainty u_c (k = 1), labelled as such, or
- the expanded uncertainty U = k * u_c with k and the approximate coverage
  probability (k = 2 is about 95 % only for an effectively normal
  distribution with adequate degrees of freedom; use Welch-Satterthwaite
  effective dof when a few small-n Type A terms dominate).

A bare plus-minus with no k and no split into statistical and systematic parts
is not usable by anyone downstream. Fix it before it reaches the paper.

## Significant figures

Round u to one or two significant figures, then round the value to the same
decimal place: 1.23456 +/- 0.0123 becomes 1.235 +/- 0.012. Keep full
precision inside the computation and round only at output — never more digits
than the uncertainty supports, and never fewer.

## In this repo

`stats_effect_size` returns intervals with `method` and `accuracy` fields;
quote those, do not restate them as exact. A number produced by a script must
have come through `analysis_run` so its inputs are hashed — an uncertainty on
an untracked number cannot be checked later.

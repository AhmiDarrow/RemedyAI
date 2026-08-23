# Convergence and refinement studies

A single-resolution simulation has an unknown discretisation error. The
refinement study is what turns it into a number with a bound.

## The procedure

1. Pick a **quantity of interest** (QoI) that the claim actually rests on
   — a drag coefficient, a peak temperature, an eigenvalue, an integrated
   flux. Convergence of a norm of the whole field is a different claim
   from convergence of the QoI; report the one you use.
2. Run at three or more resolutions with a **constant refinement ratio**
   r (r = 2 is easiest; r >= 1.3 is the usual minimum for the estimator
   to mean anything). Refine everything that should refine — halving dx
   may force dt to change in an explicit run; say whether you refined
   space and time together or separately.
3. Keep everything else fixed, with solver tolerances tightened well
   below the discretisation error you are trying to measure, and the same
   initial conditions, boundary conditions and physical model.
4. Compute the **observed order of accuracy**
   p = ln(|f3 - f2| / |f2 - f1|) / ln(r)
   for successively refined f1 (finest) ... f3 (coarsest).
5. Compare p to the design order of the scheme. Report the observed one.

## Reading the result

- **p ≈ design order** — the code is in the asymptotic range and the
  implementation is behaving.
- **p well below design order** — you are not in the asymptotic range yet
  (refine further), or a boundary condition / limiter / source term is
  lower order than the interior scheme, or there is a bug. A first-order
  boundary treatment silently caps a second-order code.
- **p above design order** — usually coincidence from being outside the
  asymptotic range, or differences dominated by round-off / solver
  tolerance rather than discretisation. Do not celebrate it.
- **Non-monotone or erratic** — insufficient smoothness (shocks,
  singularities, re-meshing between levels), or iterative tolerances
  polluting the differences. Say so rather than fitting a line to noise.

## Error estimate

With p in hand, Richardson extrapolation estimates the exact value and
hence the error in the finest run:
f_exact ≈ f1 + (f1 - f2)/(r^p - 1).

Report the **grid convergence index** (a safety-factor-scaled relative
error, the standard practice in the ASME V&V-20 procedure — check the
current edition for the exact factor rather than quoting it from memory)
alongside the QoI: "Cd = 0.412 with an estimated discretisation
uncertainty of 1.8%".

## Mechanics

Run every level through `analysis_run` with a `tag` for the study, and
keep the QoI extraction in the same script so the numbers cannot drift
from the fields. `data_diff` between two levels' output tables shows
which columns moved. The resolution table (level, cells, dx, dt, QoI,
difference, p, GCI) is the evidence — put it in the paper.

## What it does not prove

Convergence to a resolution-independent answer says the discretisation
error is small. It says nothing about whether the equations describe
reality — that is validation.

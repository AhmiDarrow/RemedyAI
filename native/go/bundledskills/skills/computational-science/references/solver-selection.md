# Solver selection

## Linear systems Ax = b

Pick from structure, not habit:

- **Dense, general** — LU with partial pivoting. Never form `inv(A)` to
  then multiply; solve. `A\b`-style calls are faster and more accurate.
- **Dense, symmetric positive definite** — Cholesky (about half the work,
  no pivoting). A failed Cholesky is a useful diagnostic: the matrix is
  not SPD, which usually means the assembly is wrong.
- **Least squares** — QR, or SVD when rank-deficient. Do **not** form the
  normal equations `A'A`; that squares the condition number.
- **Sparse, direct** — sparse LU/Cholesky with a fill-reducing ordering
  (AMD, nested dissection). Robust, memory-hungry; fill-in is the limit.
- **Sparse, iterative** — CG for SPD, MINRES for symmetric indefinite,
  GMRES/BiCGStab for nonsymmetric. **The preconditioner is the method**;
  unpreconditioned Krylov on an ill-conditioned system will stall.
  Jacobi/ILU to start, multigrid (geometric or algebraic) for elliptic
  operators — that is where O(n) scaling lives.
- Stopping on the relative residual is not stopping on the error. Report
  the tolerance and an error estimate where one is available.

## ODEs and stiffness

Stiffness = widely separated timescales, so an explicit method is forced
to tiny steps by stability rather than accuracy. Symptom: the step size
collapses while the solution is smooth.

- **Non-stiff** — explicit Runge-Kutta with adaptive steps (Dormand-
  Prince style) or Adams-Bashforth-Moulton.
- **Stiff** — implicit: BDF, Radau IIA, or an implicit RK; these need a
  Jacobian, so supply an analytic one where you can.
- **Hamiltonian / long-time energy behaviour** — symplectic integrators
  (leapfrog/Verlet, symplectic RK). A high-order non-symplectic method
  will drift in energy where a second-order symplectic one will not.
- Set `rtol` and `atol` deliberately, per component when scales differ,
  and re-run tighter to show the answer does not move.

## PDEs

Choose the discretisation for the physics: finite difference (simple
geometry, structured grids), finite volume (conservation laws, shocks —
it conserves fluxes by construction), finite element (complex geometry,
elliptic problems, variable coefficients), spectral (smooth periodic
problems, exponential accuracy, poor with shocks).

For time-dependent problems, explicit schemes obey a stability limit
(CFL for advection; a far harsher `dt ~ dx^2` for explicit diffusion).
Implicit schemes (backward Euler, Crank-Nicolson) remove the limit at the
price of a solve per step; Crank-Nicolson is second-order but can ring on
sharp fronts. Compute the CFL number and report it.

## Optimisation

Smooth with gradients → quasi-Newton (L-BFGS) or Newton with a trust
region. Constrained → interior point or SQP. Non-smooth or noisy →
a trust-region derivative-free method (Nelder-Mead is not robust in high
dimension) or stochastic search, reporting that the optimum is local.
Always report the convergence criterion actually met, the number of
function evaluations, and a multi-start check when non-convex.

## Record it

The solver, its version, the preconditioner, every tolerance and the
iteration counts belong in the run record and in the methods section.
"Solved with a standard solver" is not reproducible.

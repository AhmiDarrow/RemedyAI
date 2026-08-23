# computational-science references

- floating-point.md — IEEE 754 behaviour, cancellation, summation, conditioning vs stability; read before diagnosing any "the physics is wrong" bug.
- solver-selection.md — linear systems, ODE stiffness, PDE discretisation, optimisation: how to pick and what each choice assumes.
- convergence-studies.md — mesh and timestep refinement, observed order, Richardson extrapolation and the grid-convergence index.
- verification-and-validation.md — method of manufactured solutions, analytic limits, conservation checks; V&V separation and what each claim covers.
- benchmarking.md — warm-up, repeats, spread, what the wall clock includes, comparing implementations fairly.
- profiling.md — finding where the time goes before optimising; roofline thinking, sampling vs instrumenting, memory and I/O bound cases.
- hpc-jobs-and-scaling.md — job scripts, MPI/OpenMP layout, strong and weak scaling studies, checkpointing and reproducible parallel runs.

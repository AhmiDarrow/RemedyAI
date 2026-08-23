# HPC jobs and scaling

## Before submitting

Read the site's documentation for partitions, limits, filesystem policy
and the module system — every cluster differs, and guessing a directive
wastes a queue slot. Test the full pipeline at tiny size on an
interactive/debug allocation first. Never run heavy work on a login node.

A job script states: partition/queue, nodes, tasks per node, cpus per
task, GPUs, wall-time, memory, account, and the module or container
environment. Load modules explicitly in the script rather than relying on
an interactive shell's state — that is the most common "it ran yesterday"
failure. Under SLURM the essentials are `sbatch`, `squeue`, `sacct` (for
the *actual* memory and time used, which is what right-sizes the next
request), `scancel`, and `--array` for parameter sweeps. Do not hard-code
paths: take input and output roots from environment variables so the same
script runs on another machine.

Ask for what you need. Over-requesting wall-time and nodes lengthens the
queue wait; under-requesting kills the job at 95%.

## Parallel layout

Decide and record the decomposition: MPI ranks across nodes, OpenMP
threads within a rank, GPUs per rank. Bind and pin (`--cpu-bind`,
`OMP_NUM_THREADS`, `OMP_PROC_BIND=close`, `OMP_PLACES=cores`) — unpinned
threads migrating between NUMA domains is a routine 2x loss. Check
`OMP_NUM_THREADS` is not silently 1, or silently the whole node.

## Scaling studies

Both curves, measured, with efficiency:

- **Strong scaling** — fixed total problem, increasing processors.
  Speedup S(p) = T(1)/T(p), efficiency E = S(p)/p. Report the baseline
  T(1) honestly: it must be the *best serial implementation*, not the
  parallel code on one rank. Efficiency falls as per-rank work shrinks
  toward communication cost; report the point where E drops below a
  stated threshold (e.g. 70%) — that is the useful scaling limit.
- **Weak scaling** — fixed work *per* processor, growing problem and
  processor count together, E = T(1)/T(p). The relevant curve when the
  science question grows with the machine.

Plot both on log axes with the ideal line, give the problem size per
rank, name the hardware and interconnect, and repeat each point (see
benchmarking.md) — queue-to-queue variation on a shared machine is real.
State whether I/O is included.

When scaling stalls the causes are: communication volume or latency,
load imbalance (measure it — max vs mean rank time), serial sections
(setup, assembly, rank-0 I/O), and memory bandwidth contention.

## Determinism and reproducibility

Parallel floating-point reductions are order-dependent, so bitwise
identical output across rank counts is generally not achievable. Say that
plainly and report agreement to a stated tolerance instead of claiming
determinism. Where exact reproducibility matters, use a deterministic
reduction order and note the cost.

Record per run: commit, compiler and flags, module/container versions,
MPI implementation and version, rank/thread layout, node list, seeds and
the input deck hash. Drive submissions through `analysis_run` (or record
the job id and script into the ledger) so a figure traces back to the
allocation that produced it.

Checkpoint long runs, and test that restart-from-checkpoint reproduces an
uninterrupted run to the stated tolerance — do not assume it.

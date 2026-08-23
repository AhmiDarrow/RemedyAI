# Computational chemistry

A number from a bad geometry is still a number. The workflow below exists so
that the number means something.

## Order

1. Build a sensible starting geometry; note the conformer and protonation
   state you chose and why.
2. Conformer search before optimisation for anything flexible — an optimised
   local minimum is not the relevant conformer.
3. Optimise, then run a frequency calculation at the same level. A minimum
   has zero imaginary frequencies; a transition state has exactly one, along
   the expected coordinate. Confirm a TS with IRC.
4. Single-point energies at a higher level on the optimised geometry, if the
   protocol calls for it. Say which level gave the geometry and which gave
   the energy.
5. Thermochemical corrections at the stated temperature and pressure, with
   the frequency-scaling factor named.

Skipping step 3 is the most common way a published number turns out to be a
saddle point.

## Choices that must be reported

- **Method/functional**: HF, MP2, coupled cluster, or a DFT functional by
  name and rung. Functionals are parameterised and each fails somewhere —
  self-interaction error, static correlation, non-covalent interactions,
  barrier heights. Cite the benchmark study that supports your choice for
  this property and system class, and resolve that citation with `cite_add`;
  do not assert "B3LYP is fine here" from habit.
- **Dispersion correction**: name it (and its damping) or state that none was
  applied. For anything with stacking or a large host-guest contact, none is
  a choice with consequences.
- **Basis set**: including polarisation and diffuse functions, effective core
  potentials for heavy elements, and whether basis-set superposition error
  was corrected (counterpoise) for interaction energies.
- **Solvation**: implicit (name the model and the solvent) or explicit (how
  many molecules, chosen how). Implicit models do not capture specific
  hydrogen bonding.
- **Convergence criteria** and the SCF/geometry thresholds actually used, not
  the program defaults assumed.
- **Software name and version**, and the input files in the supporting
  information.

## Reading the output

Relative energies between structures computed at the same level are far more
trustworthy than absolute ones. Do not compare energies across different
functionals, basis sets or solvation models. Quote barriers and reaction
energies with the level attached. Where the claim is quantitative, check the
method's published error bar for that property class and say what it is —
if you do not know it, say you do not know it.

## Running it

psi4, ORCA, Gaussian, xtb, ASE and pymatgen live in the project environment,
never in Remedy's. `analysis_env(probe=True)` first, then `analysis_run` so
inputs, outputs and the software version are hashed into the ledger. Long
jobs belong on the cluster; keep the submission script and the exact input in
the repository.

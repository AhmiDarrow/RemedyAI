# Reproducibility (this is the verify step)

A result is finished when someone else regenerates it from the repository.
Until then it is a claim in a chat window.

## The five things that must be true

1. **Every number came from a recorded run.** `analysis_run` for each stage;
   `analysis_ledger(action="list")` shows the history.
   `analysis_ledger(action="verify", run_id=...)` must return INTACT before a
   number goes in the paper. DRIFTED means an input or an artifact changed
   after the run - the figure no longer matches its data. Re-run, do not
   re-hash.
2. **The environment is pinned.** Lockfile committed (uv.lock /
   requirements.txt with hashes / renv.lock / Manifest.toml / environment.yml
   with builds), or a container image referenced by digest, not by tag.
   `analysis_env` records what was actually found on the machine.
3. **Randomness is fixed and recorded.** A seed per stage, set explicitly,
   written into the run record and the paper. Where results depend on the
   seed, report across seeds rather than picking one.
4. **Data has provenance.** Source, version or access date, and a hash for
   every input file. Raw data read-only; derived data regenerable.
5. **One command rebuilds everything.** A Makefile, Snakefile, nextflow
   pipeline or a numbered script list. The README says: clone, restore the
   environment, run this, get these outputs.

## Layout that makes it easy

```text
data/raw/        read-only inputs + a manifest with hashes and sources
data/derived/    generated; deletable and regenerable
code/            one script per stage, each runnable alone
notebooks/       exploration; anything load-bearing is promoted to code/
results/         figures, tables, model outputs (generated)
manuscript/      paper source, refs.bib, refs.csl.json
plan/            preregistration, analysis plan, deviations
env/             lockfile, container definition
```

Add `.gitignore` for derived outputs, and a `CITATION.cff` so the work itself
is citable.

## Levels, named honestly

- **Repeatable** - same data, same code, same machine, same numbers.
- **Reproducible** - same data and code, a different person and machine.
- **Replicable** - new data, same design and analysis, same conclusion.

Claim only the level you have tested. A pipeline that has only ever run on
one laptop is repeatable, not reproducible - say so and test it elsewhere.

## Archiving

At submission: tag the commit, archive the repository for a DOI (Zenodo,
Software Heritage, OSF, or an institutional repository), and deposit data in
the field's registry when governance allows. Restricted data gets the access
procedure written out instead of the file.

## FAIR

Findable, Accessible, Interoperable, Reusable - the GO FAIR principles.
In practice: a persistent identifier, a licence, a README, machine-readable
metadata, and standard formats. Check the current wording at go-fair.org
rather than paraphrasing from memory.

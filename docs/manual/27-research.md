# Research — papers, analysis, citations

Point Remedy at a research project and she works the loop: sharpen the
question, find what is known, run the analysis in *your* environment, put
every number in a ledger, and refuse to call the manuscript done until the
citations resolve. Nothing to set up — notebooks, R, Julia, a Snakefile or
a `.tex` file are enough for her to notice.

## What she notices

A folder becomes a research project when she finds any of: Jupyter
notebooks, an R / Julia analysis entry, a workflow (`Snakefile`,
Nextflow, CWL), a manuscript (`*.tex` with `\documentclass`, Quarto,
`*.bib`), or a `data/` directory next to `results/` / `figures/`. Science
Python packages (`numpy`, `pandas`) on their own are not enough — that is
an ordinary code repo.

The status line of a turn then shows something like
`research: research project (notebooks, manuscript) — skills: research-method, statistics`.
Field packs ride along when the files name a field (FASTQ → bioinformatics,
BIDS → neuroscience, NetCDF → earth-climate).

## The loop

Owned by the `research-method` pack. Field packs add only what is different
in their field — they never replace the spine.

1. **Question** until it can be wrong.
2. **What is known** — `lit_search`, then `lit_fetch` for the ones that matter.
   Record the query, source, date and hit count.
3. **Hypothesis** with a refutation condition written down now.
4. **Design and analysis plan** before the data. Sample size via
   `power_analysis` (a priori). Assumptions via `stats_assumptions`.
5. **Preregister** where the field expects it (a dated plan file is the
   minimum).
6. **Analyse as planned.** Every number comes out of `analysis_run` so it
   lands in the ledger. Exploratory work is labelled exploratory.
7. **Interpret** against the effect size and its interval, in the outcome's
   units. A p-value alone is not a result.
8. **Write up** so a stranger could repeat it. `manuscript_check` against
   CONSORT / PRISMA / STROBE / ARRIVE / MDAR when those apply.
9. **Leave it reproducible.** `analysis_ledger(action="verify")` must be
   INTACT; `cite_check(resolve=true)` must PASS.

## Tools

| Tool | What it actually does |
|---|---|
| `analysis_env` | What can run *in this project*: project Python, Rscript, Julia, Quarto, papermill. Call this first. |
| `analysis_run` | One file, headlessly, in the project env (`.py` / `.R` / `.jl` / `.ipynb` / `.qmd` / `.Rmd`). Figures and tables are collected and hashed. |
| `analysis_ledger` | List / show / **verify** (re-hash inputs and artifacts — DRIFT means the figure is no longer the one that came out of this data) / diff / prune. |
| `data_profile` | Rows, types, missingness, duplicates, class balance, leakage *suspects* (never a verdict). |
| `data_diff` | Schema and distribution drift between two tables. |
| `lit_search` / `lit_fetch` | arXiv, Crossref, OpenAlex, PubMed, Semantic Scholar. Missing fields stay empty. An abstract is never presented as full text. |
| `cite_add` / `cite_import` / `cite_list` / `cite_export` | Project citation library under `.remedy-research/`. |
| `cite_check` | The gate: every citation in the manuscript must resolve. `MISMATCH` and `NO_IDENTIFIER` are reported, never quietly edited. |
| `power_analysis` | A priori power / sample size. Stdlib distributions; no scipy in the sidecar. |
| `stats_assumptions` | Normality (refused below n=20), Brown-Forsythe, outliers, and recommended tests for a *described* design. Never guesses the design. |
| `stats_effect_size` | Estimate + interval + the method that produced it. |
| `stats_multiplicity` | Holm / Bonferroni / BH (FDR) / Hochberg / BY. |
| `manuscript_check` | Evidence scan against a reporting checklist. A matching phrase is evidence the item was *mentioned*, not that reporting is adequate. |
| `manuscript_build` | Compile `.tex` (latexmk / pdflatex / …) or `.qmd` / `.Rmd` (Quarto). Condenses the TeX log. A compile is not an analysis run. |

Web literature tools use the same public web path as `web_search` (on by default). Long runs (`analysis_run`,
`manuscript_build`) sit behind the same approval gate as `bash_exec`.
Remedy does not install R, Quarto, TeX or Python packages for you —
`analysis_env` says what is missing.

## Knowledge packs

| Pack | Use when |
|---|---|
| **research-method** | Always, on research work. The spine. |
| **statistics** | Tests, p-values, effect sizes, sample size, regression |
| **ml-research** | Splits, leakage, ablations, seeds, reported metrics |
| **clinical-research** | Trials, CONSORT, randomisation, endpoints |
| **life-sciences** | Wet lab, ARRIVE, MDAR, replicates |
| **bioinformatics** | FASTQ/BAM/VCF, pipelines, genome coords |
| **neuroscience** | BIDS, imaging, spikes, multiple comparisons |
| **chemistry-research** | Structures, spectra, RDKit, computational chemistry |
| **physics-research** | Units, systematics, fitting, archives |
| **earth-climate** | NetCDF, CF, CMIP, regridding |
| **computational-science** | Solvers, HPC, verification & validation |
| **materials-and-engineering** | Characterisation, processing–structure–property |
| **social-science** | Causal inference, surveys, human subjects |
| **text-and-corpus-research** | Annotation, agreement, corpus licensing |

Packs declare their own triggers, so an ordinary coding turn does not list
them. A research-shaped ask in a research project puts `research-method`
first without you naming it.

## Honesty rules that do not yield

- No citation you have not seen resolve. No number from memory.
- "Not significant" is not "no effect".
- Post-hoc power is a function of p — if the data exist, report the
  interval.
- Dual-use, people, animals, identifiable data, money, authorship and
  submitting/posting anything are owner decisions. Remedy stops and asks.

## Learning

Same as everywhere else: procedures she picks up are graded by how the
turn went. *Allow skill creation* in Settings really does stop new ones
being written. See [Skills](07-skills.md).

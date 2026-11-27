# Pipelines: Snakemake and Nextflow

## Why the pipeline is the unit

A notebook records what you did once. A pipeline records what anyone does
every time: the inputs, the tool versions, the parameters and the order.
Once a project has more than about three steps or more than about three
samples, write the pipeline — it is cheaper than re-deriving the analysis in
six months, and it is the artefact a reviewer or a collaborator can actually
run.

## Snakemake

Rules declare outputs and inputs; the DAG is inferred backwards from the
targets.

```python
rule align:
    input:  r1="fastq/{s}_R1.fq.gz", r2="fastq/{s}_R2.fq.gz",
            idx=config["bwa_index"]
    output: bam="bam/{s}.sorted.bam"
    threads: 8
    conda:  "envs/align.yaml"
    log:    "logs/align/{s}.log"
    shell:  "bwa mem -t {threads} {input.idx} {input.r1} {input.r2} "
            "2> {log} | samtools sort -o {output.bam} -"
```

- `snakemake -n` (dry run) before every real run; it prints exactly what will
  execute and why.
- `--use-conda` (or `--software-deployment-method apptainer`) so the
  environment is part of the pipeline, not of your shell.
- `--rerun-incomplete` after a crash; `--report` builds an HTML provenance
  report worth attaching to the paper.
- Put sample lists and paths in `config.yaml` and a sample sheet, never
  hard-coded in rules.

## Nextflow

Processes connected by channels; dataflow rather than a target DAG.

- `nextflow run main.nf -profile conda,slurm -resume`. `-resume` reuses
  cached task outputs from the `work/` directory — it is the reason a
  half-finished run is not a lost day.
- `nf-core` is a curated collection of community pipelines with a shared
  structure; for a standard RNA-seq, variant or single-cell analysis, running
  a maintained nf-core pipeline usually beats writing your own, and its
  version is one thing to cite.
- The `work/` directory grows without limit; clean it deliberately, after you
  have published the results you need.

## Rules that apply to both

- **Pin the environment.** A conda env file with explicit versions, or a
  container digest. "bwa" is not a version.
- **One rule/process, one job, one log file.** Logs are outputs.
- **Never write into an input directory**; keep `raw/` read-only.
- **Set the seed** for anything stochastic and pass it as a parameter.
- Keep the pipeline, the config and the sample sheet in git; keep the data
  out of git (DVC, git-annex, or a path convention plus checksums).
- Resources per rule (`threads`, `resources: mem_mb=`) so the scheduler can
  do its job; guessing here is what fills a cluster queue with failures.

## Verifying a pipeline

The check is that a clean rerun from the same inputs produces the same
outputs. `snakemake -n` reporting nothing to do, or `nextflow -resume` with
no changed hashes, is the fast version. `analysis_ledger(action="verify")`
compares recorded input and artifact hashes and reports DRIFT per file — run
it before a figure goes into a manuscript.

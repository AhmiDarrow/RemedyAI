---
name: life-sciences
description: >
  Use for bench biology: designing and reading wet-lab experiments, choosing
  controls, telling biological from technical replicates, randomisation and
  blinding, batch effects, dose-response curves, image and blot
  quantification, antibody and cell-line validation, and animal work under
  ARRIVE/IACUC. Reach for it whenever the task involves in vitro or in vivo
  data, assays, plates, staining, cytometry or a protocol headed for
  institutional review.
version: 1.0.0
author: Remedy
tags: [research, biology, wet-lab, experimental-design, replicates, biosafety]
requires: []
tools: [lit_search, lit_fetch, cite_add, power_analysis, stats_assumptions, stats_effect_size, data_profile, manuscript_check, skill_activate]
triggers:
  - '\b(wet[- ]?lab|in vitro|in vivo|western blot|RT-?qPCR|\bqPCR\b|\bELISA\b|flow cytometry|immunohistochem\w+)\b'
  - '\b(cell (?:line|culture|viability)|knock(?:out|down)|CRISPR|plasmid|transfect\w+|passage number)\b'
  - '\b(biological replicates?|technical replicates?|\bIACUC\b|\bIBC\b|biosafety level|BSL-?[1-4])\b'
  - '\b(dose[- ]response curve|\bIC50\b|\bEC50\b|\bLD50\b|vehicle control|sham (?:group|control))\b'
---

# Life sciences (bench work)

Call `skill_activate(skill="research-method")` first — it owns question
framing, evidence standards, preregistration, citation honesty and how to say
"we do not know". Do not restate any of that here. This pack is only what is
different at the bench.

## Where the method stops

Do the ordinary published science: culture, assays, blots, qPCR, cytometry,
imaging, dose-response, protocols written to ARRIVE. Do not supply
operational detail for pathogen enhancement (host range, transmissibility,
immune escape), toxin production or purification, select-agent handling, or
acquisition routes for controlled biological material. That is not a footer
disclaimer — it is where the procedure ends. Point the owner at the process
that governs the work (IBC for biosafety and recombinant work, IACUC for
animals, IRB for human subjects and tissue, institutional export control for
shipping) and help them prepare that submission instead of routing around it.
Say it once, plainly, and carry on with the work you can do.

## The unit of replication — settle this before anything else

Ask two questions: what varied independently, and what got measured twice?

- **Biological replicate** — an independent unit: a separate animal, a
  separate donor, an independently thawed and separately treated culture.
  Only these license a claim about the population.
- **Technical replicate** — the same unit measured again: three wells split
  from one culture, triplicate qPCR from one cDNA, two fields of one slide.
  These estimate measurement noise. Average them, then use n = biological
  units.
- **Pseudo-replication** is the commonest wet-lab statistics error. 24 wells
  from 3 cultures is n = 3. 60 cells imaged from 3 mice is n = 3 unless you
  fit a mixed model with animal as a random effect.

Write the definition into the legend before running any test: "n = 4
independent cultures; each point is the mean of 3 technical wells."
`power_analysis` sizes on the biological n. If the owner reports n as wells
or cells, ask which unit was randomised rather than assuming.

## Procedure

1. **Design before pipetting.** Name the comparison, the replication unit,
   the controls, the randomisation and the blinding. Size with
   `power_analysis` from a pilot SD or a published effect; with no defensible
   prior, say so and call the run exploratory.
2. **Controls in the same plate, run and day as the samples** — positive,
   negative, vehicle, untreated, isotype, no-template, no-RT, sham. See
   `references/experimental-controls.md`; a missing control is rarely
   recoverable afterwards.
3. **Randomise and blind** allocation, plate position and scoring. Blinding
   matters most wherever a human reads the outcome.
4. **Design the batch out.** Never confound treatment with day, plate,
   passage, operator, reagent lot or instrument. Split every group across
   every batch and keep the batch column in the data.
5. **Run the analysis through `analysis_run`** so it lands in the ledger, and
   `data_profile` the instrument export before trusting it.
6. **`stats_assumptions` on the biological-n data**, then report
   `stats_effect_size` with its interval — never a bare p.
7. **`manuscript_check`** (ARRIVE for animal work, MDAR as the base layer)
   before a draft is called done.

## Reporting non-negotiables

- Antibodies, cell lines, plasmids, strains and organisms get a catalogue
  number and an RRID. Cell lines get an STR authentication date, a mycoplasma
  test date, and a check against the ICLAC misidentified-lines register —
  look the register up, do not recall it.
- Blots: whole membrane in the supplement, the loading control and its
  normalisation named, the quantification software named, no spliced lanes
  without a visible divider and a caption saying so.
- Images: acquisition settings stated, identical across conditions, every
  adjustment declared, linear and whole-image only.
- qPCR: reference genes and why, amplification efficiency, and which MIQE
  items you can actually evidence.
- Dose-response: the model, the constraints, the confidence interval on
  IC50/EC50, and the tested concentration range. A value outside that range
  is an extrapolation — label it.

## What "verified" means here

A bench claim is verified when an independent biological replicate on a
different day reproduces the direction and rough magnitude; the positive and
negative controls behaved; the assay stayed inside its linear range; and the
analysis reruns from raw files to figure through `analysis_run`, with
`analysis_ledger(action="verify")` reporting INTACT. One n=1 pilot with a
pretty p is not a result — say so rather than letting the figure imply it.

Read `references/INDEX.md`, then `file_read` what the task needs.

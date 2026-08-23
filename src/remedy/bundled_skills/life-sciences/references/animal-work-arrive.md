# Animal work: ARRIVE and the protocol

## Before any animal is ordered

Nothing starts without an approved protocol from the institution's animal
ethics body (IACUC in the US; AWERB/Home Office licence in the UK; the local
equivalent elsewhere). Without approval, the useful help is drafting the
protocol, not designing around it. Ask for the number; keep it in the methods.

The protocol states at minimum: species, strain, sex, age/weight, source,
numbers with a justification, housing and enrichment, procedures, anaesthesia
and analgesia, humane endpoints and who monitors them, method of killing, and
the 3Rs assessment (replacement, reduction, refinement).

## ARRIVE

ARRIVE (Animal Research: Reporting of In Vivo Experiments) is the reporting
guideline for animal studies, maintained by the NC3Rs and indexed by the
EQUATOR Network. Do not quote item numbers or wording from memory — open the
current checklist from EQUATOR or the ARRIVE site and copy it.
`manuscript_check(checklist="arrive")` runs the list that ships with the tool
and reports evidence found, never compliance.

Its Essential set covers, in substance: study design, sample size, inclusion
and exclusion criteria, randomisation, blinding, outcome measures,
statistical methods, animals, procedures and results — all decided **before**
the study.

## Design specifics that differ from cell work

- **The unit is usually the animal**, sometimes the cage or the litter.
  Treatment in the water or the diet makes the cage the unit; developmental
  work makes the litter the unit unless the model includes it.
- **Sample size** justified with `power_analysis` on a defensible effect,
  `dropout` for attrition, `clusters`/`icc` for cage or litter clustering.
  Ethics bodies expect the calculation, not a convention.
- **Inclusion and exclusion criteria set in advance** — health status, weight
  range, failed surgery, data quality — and every exclusion reported with its
  reason and stage. Post-hoc "outlier" removal manufactures results.
- **Randomise** allocation and cage position on the rack; light, temperature
  and disturbance vary across a rack.
- **Blind** the operator where possible and always the person scoring the
  outcome; behavioural and histological scores move most on unblinding.
- **Humane endpoints** are defined in advance with observable criteria and a
  monitoring frequency, and followed even when they cost the experiment.

## Reporting

Report every animal that entered and where each ended up (a flow diagram),
the actual n per group at analysis, the sex of the animals and whether sex
was analysed as a variable, and the welfare outcomes. State the protocol
approval number and the body that gave it. Where the analysis pools sexes or
excludes one, say why.

Dual-use limits apply here too: ordinary in vivo pharmacology, toxicology and
disease-model work is in scope; specifics serving weaponisation or deliberate
pathogen enhancement are not. See `biosafety-and-review.md`.

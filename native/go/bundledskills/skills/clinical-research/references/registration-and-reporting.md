# Registration and reporting

## Prospective registration

Register before the first participant is enrolled: ClinicalTrials.gov (NCT
ids), ISRCTN, ANZCTR, CTRI, EU CTIS, jRCT or another WHO-network registry.
Journals following ICMJE policy will not publish an unregistered trial, and
retrospective registration must be disclosed as such.

The record carries the primary outcome, its timepoint, the target sample
size and the arms. Later changes stay visible in its history — reviewers
read it. Before writing the paper, `lit_fetch` the record and diff its
primary outcome against the manuscript; a silent difference is the commonest
integrity failure in trial reporting.

Observational studies and analyses of existing data can be registered too
(OSF, EU PAS Register); systematic reviews go to PROSPERO.

## Protocol and statistical analysis plan

Write the protocol to **SPIRIT** and the SAP as a dated, version-controlled
document finalised before unblinding. Publish or deposit both; methods that
cannot be checked against a timestamped plan ask for unearned trust.

## Reporting checklists

Run `manuscript_check(path, checklist="auto")` and let it pick, or name one:

| Study type | Checklist |
|---|---|
| Randomised trial | CONSORT (+ extensions: cluster, non-inferiority, pilot, PRO, harms, AI/ML) |
| Trial protocol | SPIRIT |
| Observational (cohort, case-control, cross-sectional) | STROBE |
| Systematic review / meta-analysis | PRISMA |
| Prediction model | TRIPOD |
| Diagnostic accuracy | STARD |
| Animal work appearing in the paper | ARRIVE |

All of these are hosted by the **EQUATOR Network** (equator-network.org),
which lists the current version of each. Do not quote item numbers or
wording from memory — open the published checklist and copy it, or ship
fewer items and say so. `manuscript_check` reports evidence found for items,
never a verdict of compliance; a human confirms.

CONSORT expects the participant flow diagram to reconcile: screened →
eligible → randomised → received allocation → followed up → analysed, with
every loss counted. Build it from the data, not from memory.

## Adverse events and harms

- Pre-specify collection: how AEs are elicited (open question vs checklist —
  the method changes the rate), the coding dictionary (MedDRA), severity
  grading (CTCAE in oncology), causality assessment, and the SAE reporting
  timelines the sponsor and IRB require.
- Report harms with the same care as benefits: per arm, per participant (not
  per event alone), with denominators, severity, and withdrawals due to AEs.
  A trial reporting "well tolerated" without tables has not reported harms.
- Serious adverse events have mandatory external reporting deadlines to the
  IRB/REC and regulator. Those deadlines belong to the sponsor and are not
  something to work around.

## Results posting

Registry results posting is legally required in several jurisdictions within
a window after primary completion, published or not. Note the deadline.

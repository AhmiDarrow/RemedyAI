# Literature review

## A search someone else can rerun

Record, per source queried: the query, the source, the date, filters
(years, language, open access), and the hit count. A search you cannot
reproduce is an opinion with footnotes.

1. **Concept blocks.** Split the question into 2-4 concepts. For each,
   collect synonyms, spelling variants, acronyms, and controlled vocabulary
   where the source has it (MeSH in PubMed, ontology terms elsewhere).
2. **Combine.** OR within a block, AND across blocks. Truncation and phrase
   quoting differ per source — check each one's syntax page.
3. **Run wide first.** `lit_search(source="all")` for the shape of the field,
   then narrow. Coverage differs: Crossref/OpenAlex broad, PubMed biomedical,
   arXiv preprints in physics/CS/math/stats/econ/bio.
4. **Snowball.** Backward (reference lists of the best hits) and forward (who
   cited them). Snowballing catches what vocabulary missed.
5. **Grey literature** where the question needs it: registries, theses,
   agency reports, standards bodies. Label it grey.
6. **Stop rule.** Stop when new queries and snowballing stop producing new
   relevant records — and write down where that happened.

## Screening

Two passes, criteria written before you start:

- **Title/abstract** — include if it *might* meet criteria. Err toward
  inclusion; this pass removes only the clearly irrelevant.
- **Full text** — apply the criteria properly, recording a reason for every
  exclusion. Those counts are the flow diagram.

Keep a table: record id, decision, reason, who decided. Deduplicate on DOI,
then PMID/arXiv id, then normalised title+year — `lit_search` does this
across sources; verify near-duplicates by hand.

## Reading fast, then closely

**Fast (triage):** title, abstract, figures and their captions, the last
paragraph of the discussion, n and design in the methods. Decide: discard,
cite-for-context, or read closely.

**Closely (where a claim rests on it):** what was measured, and how; the real
n at each stage (enrolled vs analysed); the comparison group; prespecified vs
post-hoc; effect sizes and intervals, not just p-values; whether the
abstract's claim matches the results table; funding, conflicts and admitted
limitations. Note what you would need to reproduce it.

Never cite from an abstract a claim the abstract does not itself support.

## Extraction

One row per study: id/key, design, population, n, exposure, comparator,
outcome + instrument, effect + interval, registration id, funding,
risk-of-bias notes, tier (established / contested / one-lab / preprint). The
synthesis is written from this table, and every row goes through `cite_add`.

## Review types

A *systematic* review has a protocol registered before screening and a
reported search; PRISMA (EQUATOR Network) is the reporting standard — check
its current version and items on the site, never from memory. A *scoping*
review maps the field without pooling. A *narrative* review is fine, but call
it narrative.

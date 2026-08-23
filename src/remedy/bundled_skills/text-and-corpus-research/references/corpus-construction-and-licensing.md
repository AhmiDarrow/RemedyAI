# Building a corpus: sampling, licensing, provenance

## Sampling frame

Write down, before collecting: the population of texts, the frame you can
actually reach, the inclusion and exclusion rules, the time span, the
language(s) and varieties, the genres or registers, and what you could not
obtain and why. "Everything the API returned" is a frame — an opaque, ranked,
time-varying one — and any finding is about that frame until you argue
otherwise.

Design choices that decide what the corpus can answer:

- **Balanced vs opportunistic.** A balanced corpus samples registers by a
  designed proportion; an opportunistic one takes what exists. Frequency
  comparisons across subcorpora require comparable sampling in each.
- **Whole texts vs samples.** Fixed-length extracts equalise document weight;
  whole texts let long documents dominate. Say which, and report token counts
  per subcorpus.
- **Deduplication.** Syndicated news, boilerplate, reposts and near-duplicates
  inflate counts and leak between train and test splits. Deduplicate at document
  and near-duplicate level (shingling/MinHash), and report how many were
  removed.
- **Metadata is data.** Date, source, author, genre, language, and the retrieval
  method belong in a per-document table you can `data_profile`.

## Licensing, terms and law

Settle before collecting, not before publishing:

- Copyright status of the texts and of any database right in the collection.
- Platform terms of service and API terms, including whether redistribution or
  bulk storage is permitted and any rate limits.
- `robots.txt` and site scraping policies for web material; respect them.
- Personal data in the text — social media posts, forum handles, health or
  location detail — brings data-protection duties and often an ethics review
  even for public material. Public is not the same as consented.
- Text and data mining exceptions exist in some jurisdictions and are narrow;
  check the applicable rule with the institution rather than assuming.

Record the licence per source in the metadata table so that redistribution can
be decided per document later.

## Provenance and versioning

- Keep the raw fetched bytes with retrieval date, HTTP status, source URL or
  archive identifier, and a checksum.
- Version the corpus: a corpus is a moving object if it is still collecting.
  Freeze a numbered release and analyse a named version.
- If a text cannot be redistributed, plan the reproducibility package now:
  stable document IDs, the retrieval and extraction script, the preprocessing
  code, and derived aggregate data (counts, embeddings, annotations) that the
  licence does allow you to share.
- Cite existing corpora and tools by their own DOIs or the citation the
  maintainers ask for (`cite_add`), including the version.

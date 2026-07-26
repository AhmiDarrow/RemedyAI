---
name: csv-data-cleanup
version: 1.0.0
description: >
  Profile and clean CSV/TSV data: encoding, types, nulls, dedupe, report.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - data
  - csv
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: csv-data-cleanup
  official: true
  security_flags: []
---

# Csv Data Cleanup

## Steps
1. Detect encoding/delimiter.
2. Profile nulls/types/outliers.
3. Normalize dates/numbers; unify nulls; trim.
4. Dedupe on business keys if defined.
5. Write cleaned file + quality notes; keep a re-runnable script when possible.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

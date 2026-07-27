---
name: metrics-instrumentation
version: 1.0.0
description: >
  Add RED/USE-style metrics without high-cardinality label explosions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - observability
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: metrics-instrumentation
  official: true
  security_flags: []
---

# Metrics Instrumentation

## Steps
1. Pick metrics library already in repo.
2. Latency + error counters for critical paths.
3. Avoid user-id cardinality in labels.
4. Example queries in docs.
5. Alert only on actionable symptoms.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

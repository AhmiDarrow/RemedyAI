---
name: search-indexing
version: 1.0.0
description: >
  Design app search indexing and sync (FTS or search engine) with relevance checks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - search
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: search-indexing
  official: true
  security_flags: []
---

# Search Indexing

## Steps
1. Choose DB FTS vs external search.
2. Document fields/weights.
3. Write-time sync or reindex strategy.
4. UX for eventual consistency.
5. Sample relevance queries.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

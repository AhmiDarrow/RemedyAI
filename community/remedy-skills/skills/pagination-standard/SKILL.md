---
name: pagination-standard
version: 1.0.0
description: >
  Add stable list pagination (cursor preferred) with enforced max limits.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: pagination-standard
  official: true
  security_flags: []
---

# Pagination Standard

## Steps
1. Choose cursor or page; prefer cursor for large data.
2. Enforce max limit server-side.
3. Stable sort (e.g. created_at + id).
4. Return next cursor/token.
5. Document in OpenAPI.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

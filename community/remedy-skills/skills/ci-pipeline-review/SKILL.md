---
name: ci-pipeline-review
version: 1.0.0
description: >
  Review CI pipelines for caching, secret hygiene, required checks, and runtime.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ci
  - devops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: ci-pipeline-review
  official: true
  security_flags: []
---

# Ci Pipeline Review

## Checklist
1. PR + default branch triggers as needed.
2. Dependency caches keyed on lockfiles.
3. Secrets via CI store; never echoed.
4. Pin actions; prefer SHAs for high assurance.
5. Parallel jobs; fail fast on lint.
6. Align required check names with branch protection.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

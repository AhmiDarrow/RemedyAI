---
name: snapshot-test-discipline
version: 1.0.0
description: >
  Tame snapshot tests: reduce scope, review diffs, avoid golden files that hide bugs.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: snapshot-test-discipline
  official: true
  security_flags: []
---

# Snapshot Test Discipline

## Steps
1. Find large/opaque snapshots.
2. Prefer explicit assertions for logic; snapshots for stable pure serializers/UI fragments.
3. Review any snapshot update line-by-line.
4. Delete obsolete snapshots.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

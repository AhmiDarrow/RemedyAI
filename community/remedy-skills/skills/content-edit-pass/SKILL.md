---
name: content-edit-pass
version: 1.0.0
description: >
  Edit for clarity, structure, and brevity while preserving author voice.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - writing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: content-edit-pass
  official: true
  domain: content
  security_flags: []
---

# Content Edit Pass

## Steps
1. Structure pass (order, headings).
2. Clarity pass (shorten, concrete verbs).
3. Consistency (terms, tense).
4. Cut redundancy.
5. Query list for author on ambiguous claims.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

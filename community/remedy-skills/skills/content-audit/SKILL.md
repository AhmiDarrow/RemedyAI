---
name: content-audit
version: 1.0.0
description: >
  Audit existing content: freshness, accuracy, duplicates, SEO cannibalization, prune plan.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: content-audit
  official: true
  domain: content
  security_flags: []
---

# Content Audit

## Steps
1. Inventory URLs/docs.
2. Score freshness and traffic if data exists.
3. Find duplicates/overlaps.
4. Update vs redirect vs remove decisions.
5. Prioritized backlog.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

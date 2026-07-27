---
name: decision-log-personal
version: 1.0.0
description: >
  Log a personal or work decision with options, criteria, choice, review date.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - decisions
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: decision-log-personal
  official: true
  domain: personal
  security_flags: []
---

# Decision Log Personal

## Steps
1. Decision statement.
2. Options considered.
3. Criteria and weights.
4. Choice and rationale.
5. Review date and kill criteria.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

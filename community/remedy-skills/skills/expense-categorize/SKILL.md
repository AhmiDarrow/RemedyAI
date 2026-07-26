---
name: expense-categorize
version: 1.0.0
description: >
  Categorize a list of expenses and summarize by category with outliers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - finance
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: expense-categorize
  official: true
  domain: personal
  security_flags: []
---

# Expense Categorize

## Steps
1. Accept CSV/list from user.
2. Categories consistent.
3. Totals and top merchants.
4. Outliers.
5. Questions for ambiguous items.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

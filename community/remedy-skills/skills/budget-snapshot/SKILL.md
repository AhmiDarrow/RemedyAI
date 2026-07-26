---
name: budget-snapshot
version: 1.0.0
description: >
  Create a simple budget snapshot: income, fixed costs, variable, goals (no bank logins).
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
  library_id: budget-snapshot
  official: true
  domain: personal
  security_flags: []
---

# Budget Snapshot

## Steps
1. User-provided numbers only (never scrape banks).
2. Categories: fixed, variable, savings, debt.
3. Surplus/deficit.
4. 1–3 adjustment options.
5. Privacy: store only if user asks.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

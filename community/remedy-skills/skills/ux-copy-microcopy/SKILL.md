---
name: ux-copy-microcopy
version: 1.0.0
description: >
  Write UI microcopy: buttons, errors, empty states, confirmations—clear and human.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - writing
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: ux-copy-microcopy
  official: true
  domain: design
  security_flags: []
---

# Ux Copy Microcopy

## Steps
1. Inventory strings on the flow.
2. Action-oriented buttons (verb + object).
3. Errors: what happened + how to fix.
4. Empty states with next step.
5. Confirmations for irreversible actions.
6. Consistent terminology glossary.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

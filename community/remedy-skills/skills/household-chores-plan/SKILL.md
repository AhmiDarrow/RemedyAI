---
name: household-chores-plan
version: 1.0.0
description: >
  Create a household chore plan with cadence and ownership.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - home
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: household-chores-plan
  official: true
  domain: personal
  security_flags: []
---

# Household Chores Plan

## Steps
1. Spaces and tasks inventory.
2. Daily/weekly/monthly cadence.
3. Ownership split if shared.
4. Reset rituals.
5. Supply checklist.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

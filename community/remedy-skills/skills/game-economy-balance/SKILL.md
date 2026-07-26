---
name: game-economy-balance
version: 1.0.0
description: >
  Balance a game economy: sinks/faucets, inflation risks, and progression pacing.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - design
  - systems
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: game-economy-balance
  official: true
  domain: gaming
  security_flags: []
---

# Game Economy Balance

## Steps
1. Inventory currencies and items (sources and sinks).
2. Sketch earn rates per play minute for new/mid/endgame.
3. Identify inflation or soft-lock risks.
4. Propose tables or formulas (even spreadsheet-ready CSV).
5. Recommend telemetry to watch (earn, spend, time-to-goal).
6. Change only with version notes so designers can roll back.

## Deliverable
Economy notes + patch suggestions for numbers files or configs.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

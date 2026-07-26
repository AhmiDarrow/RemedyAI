---
name: game-perf-budget
version: 1.0.0
description: >
  Set performance budgets: frame time, memory, streaming, and content limits.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - perf
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-perf-budget
  official: true
  domain: gaming
  security_flags: []
---

# Game Perf Budget

## Steps
1. Target platforms and frame rates.
2. CPU/GPU/memory budgets.
3. Streaming and hitch limits.
4. Content budgets (polys, textures, draw calls) as ranges.
5. How to measure (profiling workflow in-repo tools).
6. Fail criteria for CI or nightly if exists.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

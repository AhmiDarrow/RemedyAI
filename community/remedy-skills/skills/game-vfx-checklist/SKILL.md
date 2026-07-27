---
name: game-vfx-checklist
version: 1.0.0
description: >
  Define VFX readability and performance budgets for abilities and environments.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - art
  - perf
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-vfx-checklist
  official: true
  domain: gaming
  security_flags: []
---

# Game Vfx Checklist

## Steps
1. Gameplay readability first (silhouettes, contrast).
2. Particle budgets per platform tier.
3. Overdraw and fullscreen effect limits.
4. Colorblind-safe ability colors.
5. LOD / culling notes.
6. Validation scene checklist.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

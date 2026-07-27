---
name: game-building-tools
version: 1.0.0
description: >
  Design player building tools: snap, validation, budgets, sharing limits.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - systems
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-building-tools
  official: true
  domain: gaming
  security_flags: []
---

# Game Building Tools

## Steps
1. Placement rules and snap grid.
2. Validation errors (clear messages).
3. Piece budgets and performance caps.
4. Permission in multiplayer.
5. Blueprint save/load.
6. Abuse prevention.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

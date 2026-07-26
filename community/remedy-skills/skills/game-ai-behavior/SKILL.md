---
name: game-ai-behavior
version: 1.0.0
description: >
  Design enemy or NPC AI behaviors: states, perception, difficulty layers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - ai
  - systems
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-ai-behavior
  official: true
  domain: gaming
  security_flags: []
---

# Game Ai Behavior

## Steps
1. Roles (rusher, support, sniper, civilian).
2. State machine or utility goals (idle, investigate, combat, flee).
3. Perception (sight/hearing ranges, memory).
4. Difficulty modifiers without cheap unfairness.
5. Debug visualization needs for designers.
6. Document edge cases (nav stuck, line-of-sight abuse).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: game-difficulty-design
version: 1.0.0
description: >
  Design difficulty modes and dynamic assists without breaking the fantasy.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-difficulty-design
  official: true
  domain: gaming
  security_flags: []
---

# Game Difficulty Design

## Steps
1. What “skill” means in this game.
2. Parameters that scale (HP, telegraphs, resources).
3. Avoid bullet-sponge-only modes.
4. Optional assists separate from narrative difficulty if needed.
5. Playtest script per mode.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

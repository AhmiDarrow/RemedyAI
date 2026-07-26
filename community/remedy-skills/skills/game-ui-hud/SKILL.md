---
name: game-ui-hud
version: 1.0.0
description: >
  Design HUD/information architecture: diegetic vs non-diegetic, clutter budget, combat readability.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - ui
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-ui-hud
  official: true
  domain: gaming
  security_flags: []
---

# Game Ui Hud

## Steps
1. Critical info always visible vs on demand.
2. Combat readability test (can player see threats).
3. Safe margins and scale options.
4. Console focus navigation if needed.
5. Empty/error states for menus.
6. Wireframe notes before art.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

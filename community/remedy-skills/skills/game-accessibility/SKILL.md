---
name: game-accessibility
version: 1.0.0
description: >
  Apply game accessibility: colorblind, subtitle, input remapping, difficulty assists.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - a11y
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-accessibility
  official: true
  domain: gaming
  security_flags: []
---

# Game Accessibility

## Checklist
1. Color is not the only signal.
2. Subtitle size/background options if dialogue-heavy.
3. Remappable controls; hold vs toggle options.
4. Difficulty assists without shaming.
5. Flash/motion intensity options where relevant.
6. Screen reader/UI text if platform requires.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

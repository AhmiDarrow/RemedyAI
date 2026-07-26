---
name: color-system
version: 1.0.0
description: >
  Define or refine a color system: roles (bg, text, accent, danger), contrast, dark mode.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ui
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: color-system
  official: true
  domain: design
  security_flags: []
---

# Color System

## Steps
1. Roles not raw swatches only.
2. Contrast targets for text/icons.
3. Semantic colors (success/warn/error/info).
4. Dark/light pairs if both exist.
5. Document usage do/don't.
6. Map to CSS variables or theme tokens in code if present.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

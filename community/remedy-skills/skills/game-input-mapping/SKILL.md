---
name: game-input-mapping
version: 1.0.0
description: >
  Design input mapping for keyboard/mouse/gamepad with conflicts and rebinding.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - input
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-input-mapping
  official: true
  domain: gaming
  security_flags: []
---

# Game Input Mapping

## Steps
1. Action list (not raw keys).
2. Default maps per device.
3. Conflict detection rules.
4. Hold vs tap vs double-tap.
5. Accessibility rebinding export/import.
6. Test matrix for menus vs gameplay contexts.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: typography-system
version: 1.0.0
description: >
  Set type scale, line height, and pairing rules for UI or editorial layouts.
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
  library_id: typography-system
  official: true
  domain: design
  security_flags: []
---

# Typography System

## Steps
1. Base size and scale ratio.
2. Roles: display, title, body, caption, code.
3. Line length guidance (~45–75 ch for reading).
4. Font loading strategy if web.
5. Implement tokens in styles if codebase ready.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

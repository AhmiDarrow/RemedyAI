---
name: ui-kit-inventory
version: 1.0.0
description: >
  Inventory UI components and document missing states for a kit.
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
  library_id: ui-kit-inventory
  official: true
  domain: design
  security_flags: []
---

# Ui Kit Inventory

## Steps
1. List components in use.
2. Required states matrix.
3. Gaps vs design system.
4. Naming consistency.
5. Prioritized build list.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

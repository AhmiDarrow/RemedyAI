---
name: move-house-plan
version: 1.0.0
description: >
  Plan a household move: timeline, inventory, vendors, change-of-address checklist.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - planning
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: move-house-plan
  official: true
  domain: personal
  security_flags: []
---

# Move House Plan

## Steps
1. Move date and constraints.
2. Room inventory.
3. Timeline T-30/T-7/T-1/T+1.
4. Utilities and address changes.
5. Packing order.
6. First-night box.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

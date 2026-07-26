---
name: information-architecture
version: 1.0.0
description: >
  Organize information architecture: nav, labels, findability, card sorting notes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: information-architecture
  official: true
  domain: design
  security_flags: []
---

# Information Architecture

## Steps
1. Inventory content/features.
2. Group by user mental model.
3. Nav labels (user words).
4. Cross-links and search entry points.
5. Validate with simple tree test if possible.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

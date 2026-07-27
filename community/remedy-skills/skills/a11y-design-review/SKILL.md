---
name: a11y-design-review
version: 1.0.0
description: >
  Design-side accessibility review: contrast, focus order, targets, motion, content structure.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - a11y
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: a11y-design-review
  official: true
  domain: design
  security_flags: []
---

# A11Y Design Review

## Checklist
1. Text/icon contrast.
2. Focus order matches reading order.
3. Target sizes.
4. Motion sensitivity alternatives.
5. Headings and labels planned.
6. Error identification without color alone.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

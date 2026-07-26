---
name: print-layout-basics
version: 1.0.0
description: >
  Lay out print-ready pages: margins, bleed, hierarchy, export checklist.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - print
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: print-layout-basics
  official: true
  domain: design
  security_flags: []
---

# Print Layout Basics

## Steps
1. Page size and bleed/safe margins.
2. Grid and hierarchy.
3. Image resolution guidance.
4. Color mode notes (RGB vs print CMYK handoff).
5. Preflight checklist before send-to-print.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

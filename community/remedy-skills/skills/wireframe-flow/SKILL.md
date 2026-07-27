---
name: wireframe-flow
version: 1.0.0
description: >
  Produce low-fidelity wireframe flows for a user task (text or simple structure).
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
  library_id: wireframe-flow
  official: true
  domain: design
  security_flags: []
---

# Wireframe Flow

## Steps
1. User goal and entry points.
2. Screen list with purpose.
3. Primary path + error path.
4. Notes for empty states.
5. Handoff questions for visual design.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

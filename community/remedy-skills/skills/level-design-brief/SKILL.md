---
name: level-design-brief
version: 1.0.0
description: >
  Produce a level design brief: layout goals, encounters, pacing, and greybox checklist.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - level-design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: level-design-brief
  official: true
  domain: gaming
  security_flags: []
---

# Level Design Brief

## Steps
1. Level fantasy and teaching goal (what skill is taught).
2. Beats on a timeline (quiet / peak / rest).
3. Spatial diagram description (entrances, landmarks, choke points).
4. Encounter list with difficulty intent.
5. Collectibles/secrets budget.
6. Greybox acceptance criteria before art pass.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

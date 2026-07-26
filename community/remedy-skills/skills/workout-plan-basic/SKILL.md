---
name: workout-plan-basic
version: 1.0.0
description: >
  Draft a basic workout plan with warm-up, main work, recovery (not medical advice).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - fitness
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: workout-plan-basic
  official: true
  domain: personal
  security_flags: []
---

# Workout Plan Basic

## Disclaimer
Not medical advice; user should consult professionals for health conditions.

## Steps
1. Goals and available days/equipment.
2. Split (full body / upper-lower).
3. Warm-up.
4. Main sets with progression rule.
5. Deload notes.
6. Stop rules for pain vs effort.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

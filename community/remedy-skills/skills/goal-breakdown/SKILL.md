---
name: goal-breakdown
version: 1.0.0
description: >
  Break a large goal into milestones, weekly outcomes, and first concrete actions.
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
  library_id: goal-breakdown
  official: true
  domain: personal
  security_flags: []
---

# Goal Breakdown

## Steps
1. Goal with deadline and why.
2. Success metrics.
3. Milestones.
4. Weekly outcomes.
5. Next 3 physical actions.
6. Risks and supports.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

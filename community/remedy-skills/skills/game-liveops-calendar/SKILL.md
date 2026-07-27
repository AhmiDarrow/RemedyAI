---
name: game-liveops-calendar
version: 1.0.0
description: >
  Plan live-ops events: cadence, rewards, economy impact, and rollback.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - liveops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-liveops-calendar
  official: true
  domain: gaming
  security_flags: []
---

# Game Liveops Calendar

## Steps
1. Cadence (weekly/seasonal).
2. Event goals (retention, economy sink).
3. Content checklist and feature flags.
4. Reward math vs inflation.
5. Kill switch / rollback plan.
6. Post-event metrics review.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

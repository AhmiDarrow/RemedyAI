---
name: quest-design
version: 1.0.0
description: >
  Design quests/missions with objectives, gates, rewards, and failure paths.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - narrative
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: quest-design
  official: true
  domain: gaming
  security_flags: []
---

# Quest Design

## Steps
1. Quest goal and why the player cares.
2. Objectives (primary + optional).
3. Prerequisites and world state flags.
4. Dialogue/brief beats (short).
5. Rewards and XP/loot alignment with economy.
6. Fail / abandon / retry rules.
7. Write quest sheet in project docs format.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

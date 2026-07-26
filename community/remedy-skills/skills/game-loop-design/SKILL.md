---
name: game-loop-design
version: 1.0.0
description: >
  Design or tighten a core gameplay loop with hooks, rewards, and failure states.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-loop-design
  official: true
  domain: gaming
  security_flags: []
---

# Game Loop Design

## Steps
1. State the player goal in one line.
2. Map: engage → challenge → reward → return.
3. List inputs/verbs available each step.
4. Failure: what happens, how recovery works, frustration budget.
5. Session length targets (5 / 20 / 60 minutes).
6. Document edge cases (AFK, first-time, expert).

## Avoid
Feature lists without a loop diagram or verb table.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

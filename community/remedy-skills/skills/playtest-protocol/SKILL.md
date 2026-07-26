---
name: playtest-protocol
version: 1.0.0
description: >
  Run a structured playtest: goals, tasks, observation notes, and debrief actions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - research
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: playtest-protocol
  official: true
  domain: gaming
  security_flags: []
---

# Playtest Protocol

## Steps
1. Hypotheses (what you fear is broken).
2. Task list for players (no leading).
3. Observation template (quotes, friction timestamps).
4. Severity ratings.
5. Debrief actions with owners.
6. Store notes under `docs/playtests/` or user path.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

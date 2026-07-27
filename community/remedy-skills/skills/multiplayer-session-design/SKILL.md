---
name: multiplayer-session-design
version: 1.0.0
description: >
  Design multiplayer session flow: matchmaking intent, disconnects, host migration, fairness.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - multiplayer
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: multiplayer-session-design
  official: true
  domain: gaming
  security_flags: []
---

# Multiplayer Session Design

## Steps
1. Session model (lobby, drop-in, async).
2. Party and invite flow.
3. Disconnect / reconnect rules.
4. Authority model (who decides outcomes) at high level.
5. Anti-grief basics (kick, report hooks).
6. Latency-friendly design notes (prediction needs).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: game-netcode-notes
version: 1.0.0
description: >
  Document netcode approach at a design level: prediction, reconciliation, lag compensation caveats.
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
  library_id: game-netcode-notes
  official: true
  domain: gaming
  security_flags: []
---

# Game Netcode Notes

## Steps
1. Genre constraints (twitchy vs eventual).
2. Authority and ownership of objects.
3. Prediction/reconciliation needs.
4. Cheating surfaces to watch.
5. Test plan (latency, packet loss simulation if tools exist).
6. Keep notes engine-agnostic.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

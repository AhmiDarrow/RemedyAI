---
name: game-audio-direction
version: 1.0.0
description: >
  Write audio direction: music intensity layers, SFX categories, mix priorities, implementation checklist.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - audio
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-audio-direction
  official: true
  domain: gaming
  security_flags: []
---

# Game Audio Direction

## Steps
1. Emotional targets per area/combat state.
2. SFX categories (UI, footsteps, weapons, FOLEY).
3. Ducking/priority rules (dialogue > combat > ambience).
4. Implementation hooks (events, RTPC-style parameters described generically).
5. Loudness targets for platforms.
6. Bug checklist (missing one-shots, looping leaks).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: combat-feel-tuning
version: 1.0.0
description: >
  Tune combat feel: input buffer, hitstop, feedback, and readability without engine-specific jargon lock-in.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - combat
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: combat-feel-tuning
  official: true
  domain: gaming
  security_flags: []
---

# Combat Feel Tuning

## Steps
1. List player attacks and recovery frames (or timing numbers in data).
2. Check feedback: camera, sound hooks, VFX hooks, haptics if any.
3. Input forgiveness: buffer and coyote-style timing where relevant.
4. Enemy telegraph clarity.
5. Propose numeric tweaks with before/after rationale.
6. Define a 5-minute playtest script to validate feel.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

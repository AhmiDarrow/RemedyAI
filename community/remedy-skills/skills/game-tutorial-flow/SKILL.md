---
name: game-tutorial-flow
version: 1.0.0
description: >
  Design an onboarding/tutorial that teaches verbs in context with skip options.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - ux
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-tutorial-flow
  official: true
  domain: gaming
  security_flags: []
---

# Game Tutorial Flow

## Steps
1. Core verbs to teach (max ~5 early).
2. Contextual prompts vs long text dumps.
3. Safe practice space.
4. Skip/remind later options.
5. Measure drop-off events.
6. Script of first 10 minutes.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

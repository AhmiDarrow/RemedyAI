---
name: game-design-document
version: 1.0.0
description: >
  Draft or update a game design document: pillars, loop, progression, risk, and vertical slice scope.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - design
  - docs
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-design-document
  official: true
  domain: gaming
  security_flags: []
---

# Game Design Document

## When to use
New game concept, pitch, or reconciling a messy design.

## Steps
1. Capture one-sentence pitch, audience, and platform constraints.
2. Define 3 design pillars (must guide every feature).
3. Core loop: minute-to-minute → session → meta progression.
4. Systems list (combat, economy, narrative, multiplayer) with owners/status.
5. Vertical slice: what is playable for the next milestone.
6. Open questions and risks.
7. Write or update `docs/gdd.md` (or path the user chooses).

## Done when
A teammate can build against the slice without guessing pillars.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

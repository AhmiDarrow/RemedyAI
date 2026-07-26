---
name: script-to-storyboard
version: 1.0.0
description: >
  Turn a script into a shot list / storyboard frames description.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - video
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: script-to-storyboard
  official: true
  domain: content
  security_flags: []
---

# Script To Storyboard

## Steps
1. Break script into shots.
2. Shot type (wide/medium/close), angle, motion.
3. Continuity notes.
4. Graphics/overlay callouts.
5. Estimated duration per shot.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

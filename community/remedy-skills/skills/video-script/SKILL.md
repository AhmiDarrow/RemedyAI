---
name: video-script
version: 1.0.0
description: >
  Write a video script with visual column, VO/dialogue, timing, and B-roll notes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - video
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: video-script
  official: true
  domain: content
  security_flags: []
---

# Video Script

## Steps
1. Length target and platform constraints (generic).
2. Hook in first 5–10 seconds.
3. Two-column script: visual | audio.
4. Timing estimates.
5. B-roll and on-screen text.
6. End screen CTA.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

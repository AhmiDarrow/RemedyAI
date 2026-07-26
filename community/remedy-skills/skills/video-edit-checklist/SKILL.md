---
name: video-edit-checklist
version: 1.0.0
description: >
  Checklist for editing a video cut: pacing, audio, captions, exports.
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
  library_id: video-edit-checklist
  official: true
  domain: content
  security_flags: []
---

# Video Edit Checklist

## Checklist
1. Story cut before polish.
2. Audio levels and noise.
3. Captions accuracy.
4. Pacing: remove dead air.
5. Brand lower-thirds if any.
6. Export presets per destination.
7. Thumbnail still selection.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

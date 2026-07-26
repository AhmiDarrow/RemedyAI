---
name: release-announcement
version: 1.0.0
description: >
  Write a product release announcement for blog/email/in-app.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - product
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: release-announcement
  official: true
  domain: content
  security_flags: []
---

# Release Announcement

## Steps
1. User benefits first (not feature dump).
2. What’s new / improved / fixed.
3. Who is affected.
4. How to start / migrate.
5. Links to docs.
6. Tone match brand voice.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

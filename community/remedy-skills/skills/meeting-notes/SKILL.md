---
name: meeting-notes
version: 1.0.0
description: >
  Turn discussion into structured notes: decisions, actions, owners, dates.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - work
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: meeting-notes
  official: true
  domain: personal
  security_flags: []
---

# Meeting Notes

## Steps
1. Context and attendees.
2. Decisions only if actually decided.
3. Actions with owner + due date.
4. Open questions.
5. Share summary draft.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: transcript-cleanup
version: 1.0.0
description: >
  Clean a transcript: speakers, paragraphs, filler removal, summary bullets.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - writing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: transcript-cleanup
  official: true
  domain: content
  security_flags: []
---

# Transcript Cleanup

## Steps
1. Identify speakers.
2. Paragraph by topic.
3. Light cleanup (keep meaning).
4. Mark inaudible.
5. Executive summary + action items.
6. Redact sensitive info.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

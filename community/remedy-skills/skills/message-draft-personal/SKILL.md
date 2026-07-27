---
name: message-draft-personal
version: 1.0.0
description: >
  Draft personal messages (thanks, apology, invite, check-in) with tone options.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - communication
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: message-draft-personal
  official: true
  domain: personal
  security_flags: []
---

# Message Draft Personal

## Steps
1. Intent and relationship.
2. Tone options (warm/formal/brief).
3. Drafts (2–3).
4. What to avoid.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

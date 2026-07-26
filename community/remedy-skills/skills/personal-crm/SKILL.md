---
name: personal-crm
version: 1.0.0
description: >
  Lightweight personal CRM: people notes, last contact, follow-ups (privacy first).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - social
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: personal-crm
  official: true
  domain: personal
  security_flags: []
---

# Personal Crm

## Steps
1. Fields: name, context, last contact, next follow-up.
2. Cadence rules.
3. Interaction log template.
4. Privacy: local storage preferences.
5. Weekly review of due follow-ups.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

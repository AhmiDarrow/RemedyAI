---
name: persona-profile
version: 1.0.0
description: >
  Build research-backed personas (or proto-personas) with goals, frustrations, contexts.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - research
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: persona-profile
  official: true
  domain: design
  security_flags: []
---

# Persona Profile

## Steps
1. Evidence sources (interviews, tickets, analytics)—label assumptions.
2. Goals, jobs-to-be-done, frustrations.
3. Environment and constraints.
4. Quote bank (anonymized).
5. How product decisions should change.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

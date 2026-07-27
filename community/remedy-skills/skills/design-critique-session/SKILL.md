---
name: design-critique-session
version: 1.0.0
description: >
  Facilitate a design critique: goals, evidence, actionable feedback, decisions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - process
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: design-critique-session
  official: true
  domain: design
  security_flags: []
---

# Design Critique Session

## Steps
1. Presenter states goal and constraints (2 min).
2. Silent review.
3. Feedback: observation → impact → suggestion.
4. Separate taste from usability evidence.
5. Capture decisions and follow-ups.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: frontend-a11y
version: 1.0.0
description: >
  Audit and fix high-impact accessibility issues in UI code (names, keyboard, semantics).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - a11y
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: frontend-a11y
  official: true
  security_flags: []
---

# Frontend A11Y

## Checklist
1. Controls have accessible names.
2. Images: meaningful or empty alt.
3. Keyboard order; no traps.
4. Errors tied to fields.
5. Semantic HTML first.
6. Run jsx-a11y/axe/Lighthouse when available; fix criticals first.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

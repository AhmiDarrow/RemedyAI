---
name: responsive-ui-pass
version: 1.0.0
description: >
  Fix layout breakage across mobile/tablet/desktop widths.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - css
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: responsive-ui-pass
  official: true
  security_flags: []
---

# Responsive Ui Pass

## Steps
1. Inspect layout components and breakpoints.
2. Fix overflow, clipped CTAs, fixed widths, z-index fights.
3. Prefer flex/grid + `min-width: 0`.
4. Reasonable touch targets.
5. List residual known gaps.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

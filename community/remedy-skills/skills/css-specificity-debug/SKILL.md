---
name: css-specificity-debug
version: 1.0.0
description: >
  Debug why a CSS rule loses (specificity, order, layers) and fix cleanly.
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
  library_id: css-specificity-debug
  official: true
  security_flags: []
---

# Css Specificity Debug

## Steps
1. Identify computed winning rule (devtools if available).
2. Map competing selectors and import order.
3. Fix structure/tokens before `!important`.
4. Remove dead CSS when found.
5. Document the final selector strategy.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

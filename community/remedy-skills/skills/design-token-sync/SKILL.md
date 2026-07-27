---
name: design-token-sync
version: 1.0.0
description: >
  Replace one-off colors/spacing with design tokens / CSS variables already in the project.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: design-token-sync
  official: true
  security_flags: []
---

# Design Token Sync

## Steps
1. Locate token sources (CSS vars, Tailwind theme, theme modules).
2. Grep hard-coded hex/spacing.
3. Map to tokens; propose new tokens only if repeated.
4. Verify light/dark if both exist.
5. Avoid inventing a second token system.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

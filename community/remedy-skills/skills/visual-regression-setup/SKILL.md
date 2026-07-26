---
name: visual-regression-setup
version: 1.0.0
description: >
  Add a small visual regression set for critical screens with stable snapshots.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - testing
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: visual-regression-setup
  official: true
  security_flags: []
---

# Visual Regression Setup

## Steps
1. Use existing visual tool if any (browser screenshots, visual review tools, etc.).
2. Cover a few critical screens only.
3. Disable animations; stabilize fonts when possible.
4. Document approval workflow for intentional changes.
5. Keep snapshots from becoming unmaintained noise.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

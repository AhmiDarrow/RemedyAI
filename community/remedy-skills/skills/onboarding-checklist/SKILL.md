---
name: onboarding-checklist
version: 1.0.0
description: >
  Design a dismissible first-run checklist that drives activation.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - product
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: onboarding-checklist
  official: true
  security_flags: []
---

# Onboarding Checklist

## Steps
1. 3–5 activation steps tied to value.
2. Persist completion.
3. Skip/dismiss allowed.
4. Optional funnel analytics.
5. Auto-hide when complete.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

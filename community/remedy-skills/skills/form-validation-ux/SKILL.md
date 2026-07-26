---
name: form-validation-ux
version: 1.0.0
description: >
  Improve form validation and error mapping UX (inline errors, double-submit, a11y).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: form-validation-ux
  official: true
  security_flags: []
---

# Form Validation Ux

## Steps
1. Validate on appropriate events (blur/submit).
2. Associate messages with inputs (aria-describedby).
3. Map API field errors to fields.
4. Prevent double submit; clear pending states.
5. Manage focus on success/error.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

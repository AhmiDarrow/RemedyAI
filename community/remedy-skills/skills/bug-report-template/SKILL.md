---
name: bug-report-template
version: 1.0.0
description: >
  Turn a vague bug into a reproducible report: environment, steps, expected/actual.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - product
  - docs
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: bug-report-template
  official: true
  security_flags: []
---

# Bug Report Template

## Fill
Summary · Environment · Steps · Expected · Actual · Logs (redacted) · Severity · Workaround.

## Next
Hand off to debugging once reproducible.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

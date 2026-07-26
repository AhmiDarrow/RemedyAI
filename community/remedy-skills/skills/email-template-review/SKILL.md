---
name: email-template-review
version: 1.0.0
description: >
  Review HTML emails for client safety, plain-text parts, and injection.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - email
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: email-template-review
  official: true
  security_flags: []
---

# Email Template Review

## Checklist
Inline CSS · plain-text alternative · escape user content · no scripts · auth links hygiene · document client test plan.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

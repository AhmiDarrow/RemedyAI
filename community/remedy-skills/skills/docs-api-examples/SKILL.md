---
name: docs-api-examples
version: 1.0.0
description: >
  Add runnable request/response examples to API docs for the hardest endpoints.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docs
  - api
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: docs-api-examples
  official: true
  security_flags: []
---

# Docs Api Examples

## Steps
1. Pick public or partner-facing endpoints.
2. Examples for success + common errors.
3. Keep secrets fake.
4. Ensure examples match validation rules.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

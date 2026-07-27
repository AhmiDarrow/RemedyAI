---
name: structured-logging
version: 1.0.0
description: >
  Introduce structured logging with levels, correlation IDs, and secret redaction.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: structured-logging
  official: true
  security_flags: []
---

# Structured Logging

## Steps
1. Use existing logging framework.
2. Structured fields (JSON or key=value).
3. Correct levels; correlation IDs from middleware.
4. Redact tokens/PII.
5. Replace print debugging on hot paths.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

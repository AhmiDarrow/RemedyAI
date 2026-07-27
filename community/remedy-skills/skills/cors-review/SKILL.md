---
name: cors-review
version: 1.0.0
description: >
  Review CORS settings for overly broad origins and credentialed cross-origin risks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - web
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: cors-review
  official: true
  security_flags: []
---

# Cors Review

## Steps
1. Locate CORS middleware/config.
2. Disallow `*` with credentials.
3. Allowlist exact SPA origins per environment.
4. Minimize methods/headers.
5. Verify trusted origin works and random origin fails.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

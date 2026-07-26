---
name: rate-limit-design
version: 1.0.0
description: >
  Design rate limits for public/auth endpoints with clear 429 behavior.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - security
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: rate-limit-design
  official: true
  security_flags: []
---

# Rate Limit Design

## Steps
1. Per-IP and per-user/token limits as appropriate.
2. Stricter on login/password reset.
3. Return 429 + Retry-After when possible.
4. Use existing gateway/middleware if present.
5. Document defaults and bypasses (carefully).

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: auth-session-review
version: 1.0.0
description: >
  Review login, session, JWT, or OAuth handling for common authentication flaws.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - auth
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: auth-session-review
  official: true
  security_flags: []
---

# Auth Session Review

## Checklist
1. Passwords: modern KDF only (bcrypt/argon2/scrypt).
2. JWT: verify sig, exp, aud/iss; short access TTL; secure refresh storage.
3. Rate-limit / lockout on login and reset.
4. OAuth: state/nonce; strict redirect URI allowlist.
5. Authorization checked server-side every request.
6. Logout revokes server-side session/refresh.

## Output
Issues + fixes; never include secret material.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

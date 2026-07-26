---
name: oauth-app-setup
version: 1.0.0
description: >
  Configure OAuth/OIDC clients correctly (PKCE, redirects, scopes, token storage).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - auth
  - security
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: oauth-app-setup
  official: true
  security_flags: []
---

# Oauth App Setup

## Steps
1. Strict redirect allowlist.
2. PKCE for public clients.
3. Minimal scopes.
4. Secure token storage + refresh/revoke.
5. State/nonce CSRF protections.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

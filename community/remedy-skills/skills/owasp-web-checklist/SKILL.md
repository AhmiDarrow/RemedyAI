---
name: owasp-web-checklist
version: 1.0.0
description: >
  Security-review a web change against practical OWASP-style controls (injection, XSS, authz, CSRF, SSRF).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - web
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: owasp-web-checklist
  official: true
  security_flags: []
---

# Owasp Web Checklist

## Checklist
1. Parameterized queries / no shell=True with user input.
2. XSS: encode output; audit HTML sinks.
3. Cookie flags for sessions (HttpOnly, Secure, SameSite).
4. Server-side authz on every sensitive action.
5. CSRF strategy for cookie sessions.
6. SSRF allowlists for outbound fetches.
7. Upload size/type controls if files involved.
8. No stack traces to clients in production.

## Output
Findings with severity and file references.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: api-contract-review
version: 1.0.0
description: >
  Review HTTP APIs for validation, authz, status codes, pagination, and error shape consistency.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - backend
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: api-contract-review
  official: true
  security_flags: []
---

# Api Contract Review

## Checklist
1. Boundary validation.
2. Authn/authz on sensitive routes.
3. Correct status codes and stable error JSON.
4. Max page sizes / anti-enumeration limits.
5. No prod stack traces to clients.
6. Docs/OpenAPI updated when public.

## Output
Severity-ordered findings with paths.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

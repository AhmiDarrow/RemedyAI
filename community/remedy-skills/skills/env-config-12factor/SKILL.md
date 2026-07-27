---
name: env-config-12factor
version: 1.0.0
description: >
  Refactor configuration to env-based 12-factor style with validated startup.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - config
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: env-config-12factor
  official: true
  security_flags: []
---

# Env Config 12Factor

## Steps
1. Inventory config and secrets.
2. `.env.example` without secrets.
3. Typed config validation at boot.
4. Fail fast in production on missing required vars.
5. Document variables.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

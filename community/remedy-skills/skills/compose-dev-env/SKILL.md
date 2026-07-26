---
name: compose-dev-env
version: 1.0.0
description: >
  Provide docker compose for local dependencies with healthchecks and sane ports.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docker
  - devops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: compose-dev-env
  official: true
  security_flags: []
---

# Compose Dev Env

## Steps
1. List required services (db, redis, etc.).
2. Healthchecks + depends_on conditions.
3. Volumes for data; bind-mount app if hot reload needed.
4. `.env.example` without secrets.
5. Document `up` / `down` and ports.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

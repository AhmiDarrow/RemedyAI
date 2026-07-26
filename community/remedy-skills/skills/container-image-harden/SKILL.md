---
name: container-image-harden
version: 1.0.0
description: >
  Write or harden container image recipes: multi-stage, non-root, pin bases, no secrets in layers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - container tooling
  - devops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: container-image-harden
  official: true
  security_flags: []
---

# Container Image Harden

## Checklist
1. Pin base images.
2. Multi-stage; copy runtime artifacts only.
3. Non-root USER.
4. No secrets in ENV/layers.
5. Layer order for cache; .dockerignore.
6. HEALTHCHECK when useful.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

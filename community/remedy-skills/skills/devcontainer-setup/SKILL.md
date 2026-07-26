---
name: devcontainer-setup
version: 1.0.0
description: >
  Add a devcontainer for reproducible contributor environments.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - tooling
  - docker
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: devcontainer-setup
  official: true
  security_flags: []
---

# Devcontainer Setup

## Steps
1. Base image matching runtime.
2. postCreate install deps.
3. Forward ports; document usage.
4. Keep build time reasonable.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

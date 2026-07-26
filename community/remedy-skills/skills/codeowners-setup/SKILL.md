---
name: codeowners-setup
version: 1.0.0
description: >
  Create CODEOWNERS for critical paths and align with review rules.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - git
  - process
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: codeowners-setup
  official: true
  security_flags: []
---

# Codeowners Setup

## Steps
1. Identify critical directories.
2. Map to teams/users.
3. Keep patterns accurate and minimal.
4. Ensure branch protection can require owners.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: data-export-user
version: 1.0.0
description: >
  Implement authenticated user data export with async processing if large.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - privacy
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: data-export-user
  official: true
  security_flags: []
---

# Data Export User

## Steps
1. Inventory user-owned data.
2. Async job + notification when large.
3. Expiring signed download links.
4. Rate limit.
5. Document format.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

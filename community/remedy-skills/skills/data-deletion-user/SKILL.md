---
name: data-deletion-user
version: 1.0.0
description: >
  Implement account deletion with re-auth, cascade/anonymize, and session revoke.
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
  library_id: data-deletion-user
  official: true
  security_flags: []
---

# Data Deletion User

## Steps
1. Re-authenticate.
2. Cascade or anonymize per policy.
3. Delete object storage objects.
4. Revoke sessions/tokens.
5. Audit log; optional grace period.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

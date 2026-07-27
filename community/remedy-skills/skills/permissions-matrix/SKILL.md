---
name: permissions-matrix
version: 1.0.0
description: >
  Build a role×action permission matrix and verify server enforcement.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - product
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: permissions-matrix
  official: true
  security_flags: []
---

# Permissions Matrix

## Steps
1. List roles and actions.
2. Matrix allow/deny.
3. Verify code paths match.
4. Tests for critical denies.
5. Publish matrix for support/admin.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

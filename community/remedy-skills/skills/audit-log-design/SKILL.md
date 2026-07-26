---
name: audit-log-design
version: 1.0.0
description: >
  Design audit logs for sensitive admin/user actions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - ops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: audit-log-design
  official: true
  security_flags: []
---

# Audit Log Design

## Steps
1. Event list (who/what/when/target/outcome).
2. Append-friendly storage.
3. Retention.
4. Query UI with strong authz.
5. Immutable enough for your threat model.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

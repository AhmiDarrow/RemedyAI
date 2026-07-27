---
name: multi-tenant-isolation
version: 1.0.0
description: >
  Audit multi-tenant isolation for cross-tenant data leaks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - backend
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: multi-tenant-isolation
  official: true
  security_flags: []
---

# Multi Tenant Isolation

## Checklist
1. Tenant key on owned rows.
2. Every query filters by auth-context tenant (not client-supplied alone).
3. Object storage prefixes per tenant.
4. Automated tests for cross-tenant deny.
5. Admin break-glass audited.

## Severity
Cross-tenant read/write is typically **critical**.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

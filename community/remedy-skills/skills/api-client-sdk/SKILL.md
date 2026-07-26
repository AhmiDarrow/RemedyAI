---
name: api-client-sdk
version: 1.0.0
description: >
  Generate or refresh a typed client from OpenAPI for consumers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - docs
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: api-client-sdk
  official: true
  security_flags: []
---

# Api Client Sdk

## Steps
1. Ensure OpenAPI accuracy.
2. Generate with project tool.
3. Version/publish or commit per convention.
4. Smoke critical calls.
5. Note client breaking changes.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

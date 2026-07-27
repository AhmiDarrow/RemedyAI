---
name: contract-test-api
version: 1.0.0
description: >
  Add consumer/provider contract tests so API changes don't silently break clients.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
  - api
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: contract-test-api
  official: true
  security_flags: []
---

# Contract Test Api

## Steps
1. Detect contract-test tools/OpenAPI test usage or introduce lightweight schema tests.
2. Cover critical endpoints.
3. Run in CI on PR.
4. Fail on breaking response changes.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

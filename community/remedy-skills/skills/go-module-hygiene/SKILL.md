---
name: go-module-hygiene
version: 1.0.0
description: >
  Tidy Go modules and verify reproducible builds/tests.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - go
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: go-module-hygiene
  official: true
  security_flags: []
---

# Go Module Hygiene

## Steps
1. `go mod tidy` and review go.sum.
2. Remove unnecessary `replace`.
3. `go test ./...` and `go vet ./...`.
4. Note retracted modules.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

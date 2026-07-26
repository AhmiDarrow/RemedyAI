---
name: grpc-api-design
version: 1.0.0
description: >
  Design/review gRPC protos with versioning, deadlines, and idempotency.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - grpc
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: grpc-api-design
  official: true
  security_flags: []
---

# Grpc Api Design

## Steps
1. Proto3; reserve deleted numbers.
2. Error model conventions.
3. Deadlines/cancellation.
4. Idempotent mutating RPCs.
5. Generated clients in CI.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

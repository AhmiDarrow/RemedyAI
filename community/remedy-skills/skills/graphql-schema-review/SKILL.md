---
name: graphql-schema-review
version: 1.0.0
description: >
  Review GraphQL schemas/resolvers for authz, N+1, pagination, and deprecations.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - graphql
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: graphql-schema-review
  official: true
  security_flags: []
---

# Graphql Schema Review

## Checklist
Field authz · DataLoader/batching · list pagination · deprecations before removal · depth limits if public · error shape.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

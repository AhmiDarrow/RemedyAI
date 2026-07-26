---
name: feature-flag-rollout
version: 1.0.0
description: >
  Add a feature flag with default-off rollout, metrics, and removal plan.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - product
  - release
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: feature-flag-rollout
  official: true
  security_flags: []
---

# Feature Flag Rollout

## Steps
1. Use existing flag system if any.
2. Default off in production.
3. Gate UI **and** server.
4. Success + abort metrics.
5. Remove flag after full rollout.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

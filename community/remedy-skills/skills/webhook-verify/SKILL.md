---
name: webhook-verify
version: 1.0.0
description: >
  Implement or review webhook receivers: signature verification, raw body, replay protection, idempotency.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - api
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: webhook-verify
  official: true
  security_flags: []
---

# Webhook Verify

## Steps
1. Verify HMAC/signature on **raw body** before JSON parse.
2. Reject stale timestamps (replay window).
3. Idempotent processing with event IDs.
4. Enqueue heavy work; acknowledge quickly when appropriate.
5. Tests: valid sig, invalid sig, replay.

## Critical
Parsing JSON before verifying signatures breaks many providers.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

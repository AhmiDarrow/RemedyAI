---
name: queue-consumer-safe
version: 1.0.0
description: >
  Build safe queue consumers: ack semantics, retries, DLQ, idempotent handlers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - ops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: queue-consumer-safe
  official: true
  security_flags: []
---

# Queue Consumer Safe

## Steps
1. Assume at-least-once; make handlers idempotent.
2. Ack only after success; nack/retry with backoff.
3. Max attempts → dead-letter queue.
4. Metrics: lag, failures, processing time.
5. Poison message runbook.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

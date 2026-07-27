---
name: cron-job-design
version: 1.0.0
description: >
  Design scheduled jobs with overlap locks, idempotency, and failure alerts.
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
  library_id: cron-job-design
  official: true
  security_flags: []
---

# Cron Job Design

## Checklist
1. Idempotent runs.
2. Single-runner lock if multi-instance.
3. Explicit timezone (prefer UTC).
4. Success/failure metrics + alerts.
5. Manual re-run path for ops.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: runbook-write
version: 1.0.0
description: >
  Author an on-call runbook: health checks, common failures, deploy/rollback.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docs
  - ops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: runbook-write
  official: true
  security_flags: []
---

# Runbook Write

## Sections
1. Overview & owners
2. Health verification commands
3. Symptom → diagnose → fix
4. Deploy/rollback
5. Dependencies & dashboards
6. Escalation

## Style
Copy-pastable commands; written for stressed humans.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

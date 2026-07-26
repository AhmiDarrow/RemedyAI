---
name: background-job-ui
version: 1.0.0
description: >
  Expose long-running job progress/status to users with authz and safe errors.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - product
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: background-job-ui
  official: true
  security_flags: []
---

# Background Job Ui

## Steps
1. Persist job state (queued/running/succeeded/failed/progress).
2. Users only access own jobs.
3. Poll or push updates.
4. Redact internal errors.
5. Cleanup old jobs/artifacts.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: log-level-triage
version: 1.0.0
description: >
  Triage production issues from logs: timeline, correlation IDs, dependency health.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: log-level-triage
  official: true
  security_flags: []
---

# Log Level Triage

## Steps
1. When did it start? Last deploy/config change?
2. Correlation/trace IDs for failing requests.
3. Group errors; sample multiple.
4. Check dependency health.
5. Mitigate (rollback/flag/scale) before deep fix; keep incident notes.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: backup-restore-drill
version: 1.0.0
description: >
  Plan or execute a non-destructive backup restore drill and document RTO/RPO.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - reliability
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: backup-restore-drill
  official: true
  security_flags: []
---

# Backup Restore Drill

## Steps
1. Locate backup mechanism.
2. Restore to non-prod target.
3. Boot app against restored data.
4. Record RTO/RPO achieved and gaps.
5. Schedule next drill.

## Safety
Never overwrite production without explicit confirmation.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

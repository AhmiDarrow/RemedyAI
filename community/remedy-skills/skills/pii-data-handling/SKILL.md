---
name: pii-data-handling
version: 1.0.0
description: >
  Minimize and protect PII: access, logs redaction, retention, deletion paths.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - privacy
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: pii-data-handling
  official: true
  security_flags: []
---

# Pii Data Handling

## Steps
1. Data inventory.
2. Collect only needed fields.
3. No PII in logs.
4. Access control + audits for admin.
5. Export/delete paths.
6. Encryption posture documented.

## Disclaimer
Engineering guidance, not legal advice.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

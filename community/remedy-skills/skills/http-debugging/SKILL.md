---
name: http-debugging
version: 1.0.0
description: >
  Debug HTTP failures with curl -v, status/headers, and TLS basics (redact auth).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - network
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: http-debugging
  official: true
  security_flags: []
---

# Http Debugging

## Steps
1. Reproduce with verbose curl (redact secrets).
2. Compare method/URL/headers/body vs working case.
3. Check base URL, cookies, JWT clock skew.
4. TLS cert/SNI/proxy issues.
5. Write root cause + fix.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

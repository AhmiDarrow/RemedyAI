---
name: secret-scan-guidance
version: 1.0.0
description: >
  Find likely leaked secrets in the tree and guide rotation without printing secret values.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - secrets
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: secret-scan-guidance
  official: true
  security_flags: []
---

# Secret Scan Guidance

## Steps
1. Prefer `secret scanners`, `secret scanners`, or `secret scanners` if installed.
2. Otherwise search for common patterns (AWS keys, `ghp_`, `sk-`, private key headers) and **redact** middles in output.
3. Check history only as needed; warn about force-push rewrites.
4. Remediation order: **rotate** → remove from tree → history purge only if requested.
5. Suggest pre-commit secret hooks if missing.

## Never
Echo full live credentials into chat or commits.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

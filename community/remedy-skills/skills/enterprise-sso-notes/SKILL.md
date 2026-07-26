---
name: enterprise-sso-notes
version: 1.0.0
description: >
  Enterprise SAML SSO integration checklist (metadata, assertions, JIT).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - auth
  - enterprise
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: enterprise-sso-notes
  official: true
  security_flags: []
---

# Enterprise Sso Notes

## Checklist
Metadata exchange · signature validation · attribute mapping · JIT vs invite · staging IdP · session behavior.

Use mature libraries; misconfiguration is common.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

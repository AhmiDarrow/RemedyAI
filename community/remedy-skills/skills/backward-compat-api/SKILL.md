---
name: backward-compat-api
version: 1.0.0
description: >
  Plan backward-compatible API evolution and deprecation windows.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - release
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: backward-compat-api
  official: true
  security_flags: []
---

# Backward Compat Api

## Rules
Additive preferred · deprecate before remove · version when breaking · contract tests · CHANGELOG communication.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

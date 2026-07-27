---
name: encoding-fix
version: 1.0.0
description: >
  Fix Unicode/encoding issues (UTF-8, BOM, mislabeled files).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - data
  - debug
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: encoding-fix
  official: true
  security_flags: []
---

# Encoding Fix

## Steps
1. Detect encoding.
2. Standardize UTF-8 for text unless legacy requires otherwise.
3. Explicit encoding on open in Python.
4. Regression fixture with non-ASCII.
5. Avoid silent `errors=ignore` unless accepted data loss.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

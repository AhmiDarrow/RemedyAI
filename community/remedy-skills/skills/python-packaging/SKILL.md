---
name: python-packaging
version: 1.0.0
description: >
  Package Python projects with pyproject entry points and a clean build/install check.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - python
  - packaging
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: python-packaging
  official: true
  security_flags: []
---

# Python Packaging

## Steps
1. Ensure pyproject metadata and src layout when appropriate.
2. Entry points under `[project.scripts]`.
3. `uv build` or `python -m build`.
4. Test install into a clean venv.
5. Publish only on explicit request.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

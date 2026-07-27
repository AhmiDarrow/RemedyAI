---
name: python-typing-pass
version: 1.0.0
description: >
  Raise typing quality on selected Python modules until mypy/pyright is clean.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - python
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: python-typing-pass
  official: true
  security_flags: []
---

# Python Typing Pass

## Steps
1. Run typechecker on target paths.
2. Fix real bugs first.
3. Annotate public APIs with modern syntax.
4. Avoid unjustified ignore comments.
5. Re-run until clean or document residual ignores.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

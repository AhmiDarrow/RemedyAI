---
name: cli-ux-polish
version: 1.0.0
description: >
  Polish CLI help, flags, exit codes, and non-interactive CI mode.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - cli
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: cli-ux-polish
  official: true
  security_flags: []
---

# Cli Ux Polish

## Checklist
1. Accurate `--help` with examples.
2. Exit codes meaningful.
3. `-y/--yes` or env for non-interactive.
4. stdout data / stderr logs.
5. Validate flags early.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: makefile-tasks
version: 1.0.0
description: >
  Add Makefile/task targets wrapping real project commands (setup/test/lint/run).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - tooling
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: makefile-tasks
  official: true
  security_flags: []
---

# Makefile Tasks

## Steps
1. Harvest commands from README/CI.
2. Targets: setup, test, lint, run, build, help.
3. .PHONY appropriately.
4. Keep wrappers thin.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: cross-platform-paths
version: 1.0.0
description: >
  Fix Windows/macOS/Linux path bugs using pathlib and safe joins.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - windows
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: cross-platform-paths
  official: true
  security_flags: []
---

# Cross Platform Paths

## Steps
1. Replace string path concat with pathlib/Path APIs.
2. Handle reserved Windows names when accepting filenames.
3. Reject `..` escapes on public APIs.
4. Add tests covering both separators when feasible.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

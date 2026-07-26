---
name: empty-state-design
version: 1.0.0
description: >
  Add clear empty/error/no-results states with next actions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: empty-state-design
  official: true
  security_flags: []
---

# Empty State Design

## Steps
1. Distinguish never-created vs filtered-empty vs failed load.
2. Primary action (create/import/clear filters).
3. Short neutral copy.
4. Consistent with design system components.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: pagination-ui
version: 1.0.0
description: >
  Implement accessible list pagination or load-more with URL state.
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
  library_id: pagination-ui
  official: true
  security_flags: []
---

# Pagination Ui

## Steps
1. Sync page/cursor to URL.
2. Disabled states + loading.
3. Preserve filters.
4. Prefer Load more over pure infinite scroll for a11y unless product requires otherwise.
5. Empty/error states.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

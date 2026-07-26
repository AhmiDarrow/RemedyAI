---
name: editorial-calendar
version: 1.0.0
description: >
  Build an editorial calendar with themes, owners, statuses, and deadlines.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - planning
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: editorial-calendar
  official: true
  domain: content
  security_flags: []
---

# Editorial Calendar

## Steps
1. Time horizon (month/quarter).
2. Themes mapped to launches.
3. Formats and channels.
4. Owners and due dates.
5. Status workflow (idea → draft → review → published).
6. Buffer for reactive content.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

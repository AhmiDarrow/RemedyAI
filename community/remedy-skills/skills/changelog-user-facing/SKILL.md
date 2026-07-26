---
name: changelog-user-facing
version: 1.0.0
description: >
  Turn engineering notes into user-facing release notes people understand.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - product
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: changelog-user-facing
  official: true
  domain: content
  security_flags: []
---

# Changelog User Facing

## Steps
1. Read commits/PRs/changelog drafts.
2. Translate to user outcomes.
3. Group by themes.
4. Call out breaking changes and actions required.
5. Thank contributors if culture fits.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

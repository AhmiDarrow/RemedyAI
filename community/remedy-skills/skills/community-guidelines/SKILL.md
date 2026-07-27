---
name: community-guidelines
version: 1.0.0
description: >
  Draft community guidelines: values, allowed/not allowed, enforcement ladder.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - community
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: community-guidelines
  official: true
  domain: content
  security_flags: []
---

# Community Guidelines

## Steps
1. Values and scope (where rules apply).
2. Allowed vs prohibited behaviors.
3. Reporting path.
4. Enforcement ladder (warn → restrict → ban).
5. Appeals.
6. Keep enforceable and clear.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: design-system-audit
version: 1.0.0
description: >
  Audit UI against an existing design system: drift, one-offs, missing components.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ui
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: design-system-audit
  official: true
  domain: design
  security_flags: []
---

# Design System Audit

## Steps
1. Inventory components in code vs design docs.
2. Find one-off colors/spacing/type.
3. Duplicate components with different names.
4. Accessibility gaps.
5. Prioritized cleanup list.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: design-handoff
version: 1.0.0
description: >
  Prepare design-to-engineering handoff: specs, assets, behavior notes, open questions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - process
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: design-handoff
  official: true
  domain: design
  security_flags: []
---

# Design Handoff

## Steps
1. Final flows linked.
2. Component mapping to existing system.
3. Redlines: spacing, type, states.
4. Assets exported and named.
5. Motion notes.
6. Open questions list.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: motion-design-spec
version: 1.0.0
description: >
  Specify motion: purpose, duration, easing, reduced-motion fallback.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ui
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: motion-design-spec
  official: true
  domain: design
  security_flags: []
---

# Motion Design Spec

## Steps
1. Motion purpose (orientation, feedback—not decoration only).
2. Duration scale.
3. Easing guidance.
4. Prefer reduced-motion alternatives.
5. Performance notes (avoid layout thrash).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: call-to-action-copy
version: 1.0.0
description: >
  Write CTAs matched to funnel stage with friction-aware wording.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - ux
  - marketing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: call-to-action-copy
  official: true
  domain: content
  security_flags: []
---

# Call To Action Copy

## Steps
1. Desired action and stage (aware/consider/convert).
2. Button and supporting line.
3. Reduce anxiety (price, time, cancel).
4. Variants for A/B if useful.
5. Align with actual UI behavior.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: case-study-write
version: 1.0.0
description: >
  Write a case study: problem, approach, results, proof, lessons.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - marketing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: case-study-write
  official: true
  domain: content
  security_flags: []
---

# Case Study Write

## Steps
1. Customer context (permissions!).
2. Problem and stakes.
3. Approach without confidential details.
4. Results with real metrics if allowed.
5. Quote if approved.
6. Lessons and CTA.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

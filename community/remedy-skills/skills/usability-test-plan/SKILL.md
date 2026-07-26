---
name: usability-test-plan
version: 1.0.0
description: >
  Plan a usability test: tasks, metrics, script, and synthesis template.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - research
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: usability-test-plan
  official: true
  domain: design
  security_flags: []
---

# Usability Test Plan

## Steps
1. Research questions.
2. Participant criteria.
3. Task scenarios (no leading).
4. Success metrics (completion, time, errors).
5. Moderator script.
6. Synthesis: findings → severity → actions.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

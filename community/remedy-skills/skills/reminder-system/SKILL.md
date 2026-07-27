---
name: reminder-system
version: 1.0.0
description: >
  Design a reminder system: what belongs on calendar vs tasks vs checklists.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - productivity
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: reminder-system
  official: true
  domain: personal
  security_flags: []
---

# Reminder System

## Steps
1. Types of commitments.
2. Calendar = time-bound; tasks = next actions.
3. Recurring admin.
4. Review triggers.
5. Tool-agnostic workflow.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

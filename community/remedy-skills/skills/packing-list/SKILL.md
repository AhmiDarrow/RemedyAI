---
name: packing-list
version: 1.0.0
description: >
  Generate a packing list by trip type, climate, and activities.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - travel
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: packing-list
  official: true
  domain: personal
  security_flags: []
---

# Packing List

## Steps
1. Climate and activities.
2. Clothing by day + layers.
3. Documents/meds/tech.
4. Liquids/security constraints if flying.
5. Checklist format.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: negotiation-prep
version: 1.0.0
description: >
  Prepare a negotiation: BATNA, range, script, concessions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - career
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: negotiation-prep
  official: true
  domain: personal
  security_flags: []
---

# Negotiation Prep

## Steps
1. Goals and BATNA.
2. Market context if available.
3. Ask script.
4. Concession plan.
5. Walk-away line.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

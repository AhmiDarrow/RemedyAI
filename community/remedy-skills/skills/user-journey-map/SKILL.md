---
name: user-journey-map
version: 1.0.0
description: >
  Map a user journey: stages, emotions, pain points, opportunities.
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
  library_id: user-journey-map
  official: true
  domain: design
  security_flags: []
---

# User Journey Map

## Steps
1. Persona and scenario.
2. Stages from trigger to outcome.
3. Actions, thoughts, emotions per stage.
4. Pain points and moments of delight.
5. Opportunity backlog linked to stages.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: onboarding-ui-flow
version: 1.0.0
description: >
  Design product onboarding UI: progressive disclosure, skip, value moments.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ux
  - product
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: onboarding-ui-flow
  official: true
  domain: design
  security_flags: []
---

# Onboarding Ui Flow

## Steps
1. Activation moment definition.
2. Step count budget.
3. Skip and resume.
4. Permission requests with rationale.
5. Success celebration without noise.
6. Metrics to instrument.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

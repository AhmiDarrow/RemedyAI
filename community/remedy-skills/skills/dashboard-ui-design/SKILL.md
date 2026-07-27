---
name: dashboard-ui-design
version: 1.0.0
description: >
  Design dashboards: metrics hierarchy, density, empty/loading/error, drill-down.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ui
  - data
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: dashboard-ui-design
  official: true
  domain: design
  security_flags: []
---

# Dashboard Ui Design

## Steps
1. Primary questions the dashboard answers.
2. Metric hierarchy (KPI vs supporting).
3. Defaults and time range.
4. Density and progressive disclosure.
5. Empty/loading/error states.
6. Access roles if multi-tenant.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

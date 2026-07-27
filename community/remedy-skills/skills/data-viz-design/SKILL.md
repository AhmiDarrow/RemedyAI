---
name: data-viz-design
version: 1.0.0
description: >
  Design charts/graphs for honesty: scales, color, annotations, accessibility.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - data
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: data-viz-design
  official: true
  domain: design
  security_flags: []
---

# Data Viz Design

## Steps
1. Question the chart answers.
2. Chart type fit.
3. Zero-baselines where needed; avoid misleading cuts.
4. Colorblind-safe encodings.
5. Text alternatives (table).
6. Annotation of anomalies.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

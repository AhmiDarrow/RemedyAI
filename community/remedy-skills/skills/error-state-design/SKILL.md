---
name: error-state-design
version: 1.0.0
description: >
  Design error and recovery UI that is calm, specific, and actionable.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - design
  - ux
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: error-state-design
  official: true
  domain: design
  security_flags: []
---

# Error State Design

## Steps
1. Error categories (user, network, server, permission).
2. Message pattern: what / why / next.
3. Illustration optional; clarity required.
4. Retry patterns and support links.
5. Log correlation ids for support (not scary codes alone).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

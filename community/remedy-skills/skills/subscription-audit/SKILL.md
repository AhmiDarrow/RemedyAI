---
name: subscription-audit
version: 1.0.0
description: >
  Audit subscriptions from a user-provided list: keep, cancel, downgrade recommendations.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - finance
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: subscription-audit
  official: true
  domain: personal
  security_flags: []
---

# Subscription Audit

## Steps
1. User-provided list only.
2. Monthly/annual cost normalize.
3. Usage fit.
4. Keep/cancel/downgrade.
5. Calendar reminders for renewals.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

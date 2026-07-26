---
name: privacy-checkup
version: 1.0.0
description: >
  Walk through a personal privacy checkup: app permissions, sharing, data downloads.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - privacy
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: privacy-checkup
  official: true
  domain: personal
  security_flags: []
---

# Privacy Checkup

## Steps
1. High-risk accounts list.
2. Permission audit on phone/apps.
3. Sharing/privacy settings.
4. Download-your-data if relevant.
5. Tracking reduction tips (general).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: password-hygiene
version: 1.0.0
description: >
  Personal password hygiene checklist: unique passwords, manager use, 2FA—without handling secrets.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - security
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: password-hygiene
  official: true
  domain: personal
  security_flags: []
---

# Password Hygiene

## Steps
1. Unique passwords per site (manager recommended).
2. Enable 2FA where available.
3. Recovery codes stored offline.
4. Breach response steps (rotate).
5. Never ask user to paste passwords into chat.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

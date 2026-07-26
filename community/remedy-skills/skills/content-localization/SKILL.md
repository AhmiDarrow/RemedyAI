---
name: content-localization
version: 1.0.0
description: >
  Prepare content for localization: freeze strings, context notes, do-not-translate list.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - i18n
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: content-localization
  official: true
  domain: content
  security_flags: []
---

# Content Localization

## Steps
1. Identify freezable source language.
2. Context notes for translators.
3. Do-not-translate (product names you own, code).
4. Length constraints.
5. Review cycle for high-visibility pages.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

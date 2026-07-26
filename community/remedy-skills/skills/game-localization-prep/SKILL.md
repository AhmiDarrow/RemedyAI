---
name: game-localization-prep
version: 1.0.0
description: >
  Prepare game strings for localization: keys, variables, length expansion, voice notes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - i18n
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-localization-prep
  official: true
  domain: gaming
  security_flags: []
---

# Game Localization Prep

## Steps
1. Extract player-facing strings to keys.
2. No string concatenation for sentences; use placeholders.
3. Allow ~30% length expansion in UI.
4. Gender/plural rules notes for translators.
5. Voice-over scripts separated from UI text.
6. Pseudo-loc pass to catch overflow.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

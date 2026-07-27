---
name: game-save-system
version: 1.0.0
description: >
  Design save/load: slots, versioning, cloud caveats, corruption recovery.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - systems
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-save-system
  official: true
  domain: gaming
  security_flags: []
---

# Game Save System

## Steps
1. What state must persist.
2. Slot UX and autosave policy.
3. Schema version + migration.
4. Atomic write / backup copy to limit corruption.
5. Cheating surface if online ranks matter.
6. Test plan: kill process mid-save, upgrade old save.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

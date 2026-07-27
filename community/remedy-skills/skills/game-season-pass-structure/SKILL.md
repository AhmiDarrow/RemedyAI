---
name: game-season-pass-structure
version: 1.0.0
description: >
  Structure a season pass track: free/premium split ethics, pacing, rewards (no pay-to-win).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - liveops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: game-season-pass-structure
  official: true
  domain: gaming
  security_flags: []
---

# Game Season Pass Structure

## Steps
1. Season narrative theme.
2. Free vs paid rewards (cosmetic-first if competitive).
3. XP pacing curve.
4. Catch-up policy.
5. Economy safety.
6. Player communication plan.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

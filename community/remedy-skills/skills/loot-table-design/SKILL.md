---
name: loot-table-design
version: 1.0.0
description: >
  Design loot tables with drop rates, pity systems, and economy safety.
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
  library_id: loot-table-design
  official: true
  domain: gaming
  security_flags: []
---

# Loot Table Design

## Steps
1. Rarity tiers and intended drop feel.
2. Conditional tables (biome, boss, chest).
3. Pity / bad-luck protection if used.
4. Duplicate handling.
5. Audit expected value vs craft costs.
6. Publish rates clearly if the product requires disclosure.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

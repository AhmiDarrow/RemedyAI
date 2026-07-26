---
name: travel-itinerary
version: 1.0.0
description: >
  Build a travel itinerary: logistics, buffers, offline notes, packing constraints.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - travel
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: travel-itinerary
  official: true
  domain: personal
  security_flags: []
---

# Travel Itinerary

## Steps
1. Dates, travelers, budget band.
2. Transport and lodging blocks.
3. Day plans with buffers.
4. Booking reference placeholders.
5. Offline critical info.
6. Contingency (delays).

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

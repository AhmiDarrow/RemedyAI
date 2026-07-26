---
name: adr-write
version: 1.0.0
description: >
  Write an Architecture Decision Record for a significant technical choice.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docs
  - architecture
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: adr-write
  official: true
  security_flags: []
---

# Adr Write

## Template
Title · Date · Status · Context · Decision · Alternatives · Consequences.

## Steps
Save under `docs/adr/` or project convention; link from architecture docs.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

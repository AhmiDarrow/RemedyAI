---
name: fixture-factory
version: 1.0.0
description: >
  Create maintainable test factories/fixtures instead of brittle object literals everywhere.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: fixture-factory
  official: true
  security_flags: []
---

# Fixture Factory

## Steps
1. Find repeated test setup.
2. Introduce factories with overrides (factory_boy, fishery, rosie, etc. or simple helpers).
3. Keep defaults valid minimal objects.
4. Refactor a few tests to prove ergonomics.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

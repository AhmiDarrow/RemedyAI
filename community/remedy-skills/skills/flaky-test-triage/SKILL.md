---
name: flaky-test-triage
version: 1.0.0
description: >
  Reproduce and fix flaky tests: races, time, order dependence, shared state.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: flaky-test-triage
  official: true
  security_flags: []
---

# Flaky Test Triage

## Steps
1. Capture failure log and any seed/order info.
2. Re-run the single test in a loop when possible.
3. Hunt shared state, sleeps, network, unordered collections, parallel races.
4. Fix with isolation, fake clocks, condition waits, stable sorting.
5. Document non-obvious flake cause briefly.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

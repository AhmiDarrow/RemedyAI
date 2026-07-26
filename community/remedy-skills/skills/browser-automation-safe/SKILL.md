---
name: browser-automation-safe
version: 1.0.0
description: >
  Automate browser checks with browser-test best practices (stable selectors, no fixed sleeps).
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - browser
  - e2e
  - testing
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: browser-automation-safe
  official: true
  security_flags: []
---

# Browser Automation Safe

## Steps
1. Prefer the browser test runner if present.
2. Role/text selectors over brittle CSS when possible.
3. Wait for conditions, not `sleep`.
4. Isolate test data; production clicks only with explicit approval.
5. Save trace/screenshot on failure.

## Safety
No purchases or destructive admin automation without confirmation.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

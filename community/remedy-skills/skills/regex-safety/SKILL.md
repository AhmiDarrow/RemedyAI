---
name: regex-safety
version: 1.0.0
description: >
  Review regexes for ReDoS and correctness on untrusted input.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: regex-safety
  official: true
  security_flags: []
---

# Regex Safety

## Steps
1. Find regex on user input.
2. Simplify or replace catastrophic patterns.
3. Bound input length.
4. Tests with long adversarial strings.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

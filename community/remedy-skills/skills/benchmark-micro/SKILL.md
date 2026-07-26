---
name: benchmark-micro
version: 1.0.0
description: >
  Create a trustworthy microbenchmark for a profiled hot function.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - perf
  - testing
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: benchmark-micro
  official: true
  security_flags: []
---

# Benchmark Micro

## Steps
1. Confirm hotspot via profiling first.
2. Use proper harness for the language.
3. Realistic inputs; prevent DCE pitfalls.
4. Report distributions, not single runs.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

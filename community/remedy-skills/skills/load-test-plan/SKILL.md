---
name: load-test-plan
version: 1.0.0
description: >
  Design and run a minimal load test on critical endpoints with clear stop conditions.
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
  library_id: load-test-plan
  official: true
  security_flags: []
---

# Load Test Plan

## Steps
1. Choose 1–3 endpoints + realistic mix.
2. Use load generators/vegeta/hey/locust if available.
3. Ramp; watch p95 and error rate.
4. Stop on error storms; capture bottleneck hypothesis.
5. Report numbers + next optimizations.

## Caution
Don't overload shared prod/staging without permission.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

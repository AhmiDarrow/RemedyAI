---
name: perf-profile-cpu
version: 1.0.0
description: >
  Capture and interpret a CPU profile to find hot functions before optimizing.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - perf
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: perf-profile-cpu
  official: true
  security_flags: []
---

# Perf Profile Cpu

## Steps
1. Reproduce load.
2. Capture profile (pprof, py-spy, perf, Chrome CPU profile, etc.).
3. Identify top cumulative samples.
4. Optimize with measurement, not guesses.
5. Compare before/after profiles.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: rust-clippy-fix
version: 1.0.0
description: >
  Run Clippy and fix correctness-oriented lints; re-test.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - rust
  - quality
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: rust-clippy-fix
  official: true
  security_flags: []
---

# Rust Clippy Fix

## Steps
1. `cargo clippy` with project-standard flags.
2. Fix real bug lints first.
3. Keep style consistent with the crate.
4. Re-run tests.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

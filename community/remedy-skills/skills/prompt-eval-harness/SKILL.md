---
name: prompt-eval-harness
version: 1.0.0
description: >
  Build a small regression suite for prompts/agent behaviors with deterministic checks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - llm
  - testing
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: prompt-eval-harness
  official: true
  security_flags: []
---

# Prompt Eval Harness

## Steps
1. 10–30 fixtures with expected properties.
2. Prefer schema/contains/forbid checks over pure vibes.
3. Script runner producing JSON results.
4. Gate critical cases in CI when feasible.
5. No real PII in fixtures.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

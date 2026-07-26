---
name: tool-use-spec
version: 1.0.0
description: >
  Specify safe tool/function contracts: schemas, side effects, confirmations, timeouts.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - llm
  - tools
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: tool-use-spec
  official: true
  security_flags: []
---

# Tool Use Spec

## Steps
1. List tools with JSON schemas and side-effect class.
2. Confirm destructive tools.
3. Timeouts/retries/error shapes.
4. Redact secrets in logs.
5. Unit-test allowlists (URL/fs).

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

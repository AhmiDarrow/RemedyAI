---
name: release-checklist
version: 1.0.0
description: >
  Execute a pre-release gate: dirty tree, version alignment, tests, docs, remaining ship steps.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - release
  - ci
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: release-checklist
  official: true
  security_flags: []
---

# Release Checklist

## When to use
User says ship, release, pre-flight, or cut a tag.

## Checklist
1. `git status` — list dirty files; confirm intentional.
2. Align version surfaces (pyproject, package.json, Cargo.toml, sync scripts).
3. Run the project's test command (pytest / npm test / cargo test / go test).
4. Run lint/typecheck if present.
5. Confirm CHANGELOG has this version.
6. List manual steps left: tag `vX.Y.Z`, CI, publish, desktop release.

## Output
Pass/fail table with commands and blockers. Do **not** tag or publish unless explicitly asked.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: changelog-entry
version: 1.0.0
description: >
  Author a Keep-a-Changelog entry from commits/diff for a version bump or release.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docs
  - release
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: changelog-entry
  official: true
  security_flags: []
---

# Changelog Entry

## When to use
Shipping a version and CHANGELOG must be updated.

## Steps
1. Read existing `CHANGELOG.md` (or scaffold Keep a Changelog).
2. Collect commits since last tag: `git log vX.Y.Z..HEAD --oneline`.
3. Classify: Added / Changed / Fixed / Security / Deprecated / Removed.
4. Write **user-facing** entries (impact), not raw commit subjects.
5. Prepend `## [X.Y.Z] - YYYY-MM-DD` and show the patch before writing to disk.

## Avoid
Listing pure refactors with no user or ops impact unless config/CLI breaks.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

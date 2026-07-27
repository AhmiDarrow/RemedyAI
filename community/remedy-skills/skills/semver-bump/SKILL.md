---
name: semver-bump
version: 1.0.0
description: >
  Recommend major/minor/patch from the change set and apply a consistent version bump.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - release
  - versioning
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: semver-bump
  official: true
  security_flags: []
---

# Semver Bump

## When to use
Version bump or "is this major or patch?".

## Steps
1. Diff vs last tag: breaking → major; feature → minor; fix → patch.
2. Prefer project sync script if present (`scripts/sync_version.py`, `npm version`, etc.).
3. Propose version + rationale; apply after confirm (or immediately if user already chose).
4. List files touched; remind CHANGELOG + tag after tests.

## Never
Force-push tags or publish without an explicit request.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: user-story-split
version: 1.0.0
description: >
  Split an epic into vertical, testable user stories with acceptance criteria.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - product
  - planning
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: user-story-split
  official: true
  security_flags: []
---

# User Story Split

## Steps
1. Restate user outcome.
2. Vertical slices (shippable value).
3. Acceptance criteria per story.
4. Order by riskiest assumption.
5. Non-goals and dependencies called out.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: acceptance-criteria
version: 1.0.0
description: >
  Write testable acceptance criteria for a feature or bugfix.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - product
  - testing
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: acceptance-criteria
  official: true
  security_flags: []
---

# Acceptance Criteria

## Rules
Observable outcomes · edge cases · auth roles · negatives · limits if relevant.

## Format
Numbered list verifiable by QA or an agent.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

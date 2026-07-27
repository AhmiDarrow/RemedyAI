---
name: incident-postmortem
version: 1.0.0
description: >
  Write a blameless postmortem with timeline, root cause, and owned actions.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - docs
kind: native
status: discovered
tools:
  - file_write
  - file_read
metadata:
  source: library
  library_id: incident-postmortem
  official: true
  security_flags: []
---

# Incident Postmortem

## Sections
Summary · Impact · Timeline (UTC) · Root cause · What went well/poorly · Actions (owner, date) · Detection gaps.

## Tone
Blameless; systems over individuals.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

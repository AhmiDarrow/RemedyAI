---
name: sla-error-budget
version: 1.0.0
description: >
  Define practical SLIs/SLOs and an error-budget policy for a service.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - sre
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: sla-error-budget
  official: true
  security_flags: []
---

# Sla Error Budget

## Steps
1. User-centric SLIs.
2. Realistic SLO.
3. Error budget + deploy policy when burned.
4. Dashboards + burn alerts.
5. Monthly review cadence.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

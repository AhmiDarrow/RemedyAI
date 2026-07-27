---
name: threat-model-lite
version: 1.0.0
description: >
  Write a one-page threat model for a feature: assets, actors, entry points, mitigations.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - design
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: threat-model-lite
  official: true
  security_flags: []
---

# Threat Model Lite

## Steps
1. Assets to protect.
2. Actors (anon, user, admin, automated abuse).
3. Entry points (HTTP, jobs, webhooks, files).
4. Top abuse cases.
5. Mitigations + residual risk.
6. Store under `docs/` or PR description.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

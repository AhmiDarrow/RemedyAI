---
name: nodejs-upgrade
version: 1.0.0
description: >
  Plan and execute a Node.js runtime upgrade with CI and dependency checks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - node
  - upgrade
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: nodejs-upgrade
  official: true
  security_flags: []
---

# Nodejs Upgrade

## Steps
1. Read engines + CI node version.
2. Check native addons / engines constraints.
3. Upgrade, install, test, build.
4. Update CI and docs.
5. Note breaking Node changes affecting the app.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

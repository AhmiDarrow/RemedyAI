---
name: license-compliance
version: 1.0.0
description: >
  Summarize third-party licenses and flag strong copyleft risk for distribution.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - legal
  - deps
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: license-compliance
  official: true
  security_flags: []
---

# License Compliance

## Steps
1. Find manifests and existing NOTICE files.
2. Use license scanners when available (`license-checker`, `pip-licenses`, `cargo license`).
3. Group: permissive / weak copyleft / strong copyleft / unknown.
4. Explain distribution implications in plain language (**not legal advice**).
5. List attribution obligations for binaries.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

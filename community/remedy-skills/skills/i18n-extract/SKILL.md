---
name: i18n-extract
version: 1.0.0
description: >
  Extract UI strings into i18n catalogs and find missing locale keys.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - i18n
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: i18n-extract
  official: true
  security_flags: []
---

# I18N Extract

## Steps
1. Detect i18n framework.
2. Extract strings; use placeholders (ICU) not concatenation.
3. Find missing keys in other locales.
4. Keep catalogs consistent/sorted per convention.
5. Note RTL only if relevant locales exist.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

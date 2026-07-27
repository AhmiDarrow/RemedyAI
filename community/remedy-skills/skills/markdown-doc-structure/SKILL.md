---
name: markdown-doc-structure
version: 1.0.0
description: >
  Restructure Markdown documentation for clear heading hierarchy and working links.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - docs
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: markdown-doc-structure
  official: true
  security_flags: []
---

# Markdown Doc Structure

## Steps
1. Outline H1–H3.
2. Single H1; logical nesting.
3. Fix links and code fence languages.
4. TOC for long pages if needed.
5. Remove stale version claims.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

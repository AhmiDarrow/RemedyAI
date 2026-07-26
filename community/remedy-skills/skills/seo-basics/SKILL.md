---
name: seo-basics
version: 1.0.0
description: >
  Apply basic technical SEO checks to marketing/docs pages.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - marketing
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: seo-basics
  official: true
  security_flags: []
---

# Seo Basics

## Checklist
Titles/descriptions · canonicals · robots/noindex intent · heading hierarchy · sitemap · basic LCP sanity.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

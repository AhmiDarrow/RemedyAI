---
name: json-schema-design
version: 1.0.0
description: >
  Design tight JSON Schema / Zod / Pydantic models with bounds and examples.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - data
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: json-schema-design
  official: true
  security_flags: []
---

# Json Schema Design

## Steps
1. Collect sample payloads.
2. Required fields, enums, formats, max lengths.
3. Limit array sizes; consider additionalProperties false.
4. Examples + invalid fixtures for tests.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

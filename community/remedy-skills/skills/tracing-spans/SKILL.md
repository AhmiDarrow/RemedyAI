---
name: tracing-spans
version: 1.0.0
description: >
  Add distributed tracing spans across request and outbound calls.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - ops
  - observability
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: tracing-spans
  official: true
  security_flags: []
---

# Tracing Spans

## Steps
1. Detect OpenTelemetry/tracing setup.
2. Span HTTP/DB/tool calls with useful attributes.
3. Propagate context across async/threads.
4. Sampling suitable for prod.
5. Verify in the project's trace UI.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

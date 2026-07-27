---
name: money-calculations
version: 1.0.0
description: >
  Implement money math with integers/decimals, explicit rounding, and currency codes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - finance
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: money-calculations
  official: true
  security_flags: []
---

# Money Calculations

## Rules
1. No binary floats for money.
2. Integer minor units or Decimal.
3. Document rounding mode.
4. Store currency with amounts.
5. Invariant tests (line sums, tax).

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

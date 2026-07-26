---
name: payment-webhook-flow
version: 1.0.0
description: >
  Implement payment webhooks with verification and idempotent entitlement updates.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - payments
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: payment-webhook-flow
  official: true
  security_flags: []
---

# Payment Webhook Flow

## Steps
1. Verify signatures on raw body.
2. Handle subscription lifecycle events needed by the product.
3. Idempotent writes of entitlements.
4. Never trust client-only payment success.
5. Fixture tests with provider samples.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

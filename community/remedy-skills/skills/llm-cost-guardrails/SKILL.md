---
name: llm-cost-guardrails
version: 1.0.0
description: >
  Add token/cost/latency guardrails and sensible model routing.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - llm
  - cost
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: llm-cost-guardrails
  official: true
  security_flags: []
---

# Llm Cost Guardrails

## Steps
1. Meter tokens per path.
2. Cap max tokens; summarize contexts deliberately.
3. Cache embeddings/repeated prompts when safe.
4. Cheap models for classify; stronger for hard coding.
5. Budgets/alerts for multi-tenant.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

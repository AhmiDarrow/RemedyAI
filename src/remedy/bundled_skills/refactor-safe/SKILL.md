---
name: refactor-safe
description: Small, behavior-preserving refactors with verification.
version: 1.0.0
author: Remedy
tags: [refactor, cleanup]
---

# Safe Refactor

## When to use
"clean this up", rename, extract helper, reduce duplication — without changing behavior.

## Steps
1. Confirm intent and scope; avoid drive-by rewrites.
2. Read surrounding tests/callers before editing.
3. Make small atomic edits; keep public APIs stable unless asked.
4. Re-run targeted tests or typecheck when available.
5. Summarize what changed and what was deliberately left alone.

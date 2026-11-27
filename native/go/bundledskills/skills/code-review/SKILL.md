---
name: code-review
description: Structured code review of files, diffs, or recent changes with severity-ranked findings.
version: 1.0.0
author: Remedy
tags: [review, quality, security]
---

# Code Review

## When to use
User asks to review code, a PR, a module, or "is this safe / correct".

## Steps
1. Identify scope (paths or git diff). Prefer reading actual files with tools.
2. Check correctness, edge cases, security, error handling, tests, and maintainability.
3. Report findings by severity: **Blocker / Major / Minor / Nit**.
4. For each finding: location, why it matters, concrete fix suggestion.
5. End with a short overall assessment (ship / fix-first / needs tests).

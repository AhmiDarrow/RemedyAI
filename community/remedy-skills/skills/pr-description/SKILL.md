---
name: pr-description
version: 1.0.0
description: >
  Draft a precise PR title and body from branch commits and diff. Use before opening or updating a pull request.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - git
  - pr
  - docs
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: pr-description
  official: true
  security_flags: []
---

# Pr Description

## When to use
User asks to open a PR, write a PR description, or summarize a branch for review.

## Steps
1. Run `git status`, `git log --oneline main..HEAD` (or `master..HEAD`), and `git diff main...HEAD --stat`.
2. Skim the full diff for user-facing behavior, risk areas, and tests.
3. Write:
   - **Title**: imperative, ≤72 characters
   - **Summary**: 2–4 sentences of *why* and *what*
   - **Changes**: concrete bullets (not "misc fixes")
   - **Test plan**: commands run and expected results
   - **Risk / rollout** if migrations or flags
4. Do not invent features absent from the diff. Link issue IDs if present in commits.
5. Offer paste-ready markdown; run `gh pr create` only if asked and `gh` works.

## Done when
User has a PR body they can submit without guessing what changed.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

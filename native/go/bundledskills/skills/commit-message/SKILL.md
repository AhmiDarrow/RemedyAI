---
name: commit-message
description: Draft a clear conventional commit message from the current diff.
version: 1.0.0
author: Remedy
tags: [git, commit, writing]
---

# Commit Message

## When to use
User asks for a commit message or is ready to commit.

## Steps
1. Inspect `git status` and `git diff` / `git diff --staged`.
2. Group changes into one logical commit theme when possible.
3. Propose subject line ≤72 chars (conventional style when it fits: feat/fix/docs/chore).
4. Optional body: why, not every file list.
5. Do not run `git commit` unless the user explicitly requests it.

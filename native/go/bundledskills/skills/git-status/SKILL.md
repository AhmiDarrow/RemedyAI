---
name: git-status
description: Inspect git branch, status, and recent commits to ground the next change.
version: 1.0.0
author: Remedy
tags: [git, vcs]
---

# Git Status

## When to use
Before commits, when unsure what's dirty, or planning a change.

## Steps
1. Run `git status -sb` and `git branch -vv` (via bash_exec) if git is available.
2. Optionally `git log -5 --oneline` and `git diff --stat`.
3. Summarize branch, tracking, dirty files, and risk (uncommitted work).
4. Do not commit or force-push unless the user explicitly asks.

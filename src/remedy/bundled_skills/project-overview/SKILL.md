---
name: project-overview
description: Map a codebase quickly — layout, stack, entry points, and how to run it.
version: 1.0.0
author: Remedy
tags: [project, explore, onboarding]
---

# Project Overview

## When to use
"review project", "what's this repo", "explain the structure", first look at a workspace.

## Steps
1. `list_dir` on the project root (ignore node_modules/.git/dist build artifacts when summarizing).
2. Read `README.md` and package manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.).
3. List top-level `src/` (or app/lib) to find entry points.
4. Summarize: purpose, stack, layout, how to run/test, and 2–3 next actions.
5. Use real tools — never invent file trees.

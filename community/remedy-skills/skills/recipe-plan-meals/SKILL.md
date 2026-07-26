---
name: recipe-plan-meals
version: 1.0.0
description: >
  Plan meals for N days given constraints (time, diet, servings) with shopping list.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - personal
  - food
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: recipe-plan-meals
  official: true
  domain: personal
  security_flags: []
---

# Recipe Plan Meals

## Steps
1. Constraints and preferences.
2. Meal slots.
3. Recipes or simple meal ideas.
4. Consolidated shopping list.
5. Prep-ahead tips.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: write-tests
description: Add focused unit or integration tests for new or changed behavior.
version: 1.0.0
author: Remedy
tags: [testing, quality]
---

# Write Tests

## When to use
User asks for tests, coverage of a bugfix, or validation of a feature.

## Steps
1. Find the existing test layout and framework (pytest, jest, etc.).
2. Read the code under test; identify public behavior and edge cases.
3. Add minimal tests that fail without the fix / prove the feature.
4. Run the relevant test subset if possible; report results.
5. Prefer clear names and one behavior per test.

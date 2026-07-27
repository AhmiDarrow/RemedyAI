---
name: hello-library
version: 1.0.0
description: >
  Demo community skill from the Remedy Skills Library. Prints a short
  greeting via its script — safe for quarantine/trust smoke tests.
author: Remedy
tags:
  - demo
  - library
  - example
kind: native
status: discovered
tools: []
requires: []
metadata:
  library_id: hello-library
  source: library
  security_flags: []
---

# Hello Library

A minimal skill used to dogfood the Skills Library (catalog → install → quarantine → trust).

## When to use

- Verify library install works end-to-end
- Confirm quarantine blocks scripts until Trust

## Script

`scripts/greet.py` prints a one-line greeting (no network, no shell).

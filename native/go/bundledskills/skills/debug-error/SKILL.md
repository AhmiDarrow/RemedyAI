---
name: debug-error
description: Triage stack traces and failures — reproduce, locate root cause, propose a minimal fix.
version: 1.0.0
author: Remedy
tags: [debug, errors, troubleshooting]
---

# Debug Error

## When to use
Paste of traceback, "tests fail", "it crashes", failing command output.

## Steps
1. Extract the error type, message, and top relevant frames.
2. Open the cited source files at the failing lines.
3. Form a hypothesis; verify with nearby code and related tests.
4. Propose the smallest fix; implement if asked.
5. Suggest a regression test when appropriate.

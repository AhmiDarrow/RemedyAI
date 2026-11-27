---
name: remember-me
description: Capture durable user facts and preferences into Remedy memory and profile.
version: 1.0.0
author: Remedy
tags: [memory, companion, personalization]
---

# Remember Me

## When to use
User says "remember that…", "I prefer…", "always…", or shares stable personal/project facts.

## Steps
1. Restate the fact briefly for confirmation (unless they said "just remember it").
2. Call `memory_save` with clear content and a short title.
3. Prefer category tags: `preference`, `work`, `personal`, `project`.
4. Confirm what was stored in one short line.

## Do not
- Store secrets (API keys, passwords) without explicit user request.
- Overwrite high-confidence facts with weak guesses.

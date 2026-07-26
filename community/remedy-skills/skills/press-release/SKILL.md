---
name: press-release
version: 1.0.0
description: >
  Draft a press release: headline, lede, body, boilerplate, quotes, links.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - content
  - comms
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: press-release
  official: true
  domain: content
  security_flags: []
---

# Press Release

## Steps
1. Newsworthy angle (what changed for whom).
2. Headline + subhead.
3. Lede with 5 Ws.
4. Supporting facts and optional quote.
5. Boilerplate about the org.
6. Contact and links.
7. Fact-check all claims.

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.

---
name: sbom-generate
version: 1.0.0
description: >
  Generate a Software Bill of Materials (CycloneDX/SPDX) using available tooling.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - supply-chain
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: sbom-generate
  official: true
  security_flags: []
---

# Sbom Generate

## Steps
1. Detect ecosystems in the repo.
2. Prefer `SBOM tools`, `SBOM tools-*`, or language SBOM tools.
3. Write SBOM under `dist/` or user path.
4. Document regeneration command.
5. If tools missing, provide install commands (install only with approval).

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

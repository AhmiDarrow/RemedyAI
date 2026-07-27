---
name: dependency-audit
version: 1.0.0
description: >
  Audit project dependencies for known vulnerabilities and outdated high-risk packages.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - deps
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: dependency-audit
  official: true
  security_flags: []
---

# Dependency Audit

## Steps
1. Detect lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`, `uv.lock`, `go.mod`/`go.sum`, `poetry.lock`).
2. Run the matching audit command when the ecosystem tool is available:
   - Node: `npm audit` or `pnpm audit` (or `yarn npm audit`)
   - Python: `pip-audit` or `uv pip audit` / `poetry show` + advisory lookup
   - Rust: `cargo audit`
   - Go: `govulncheck ./...`
3. Summarize **high/critical** first: package, issue, fixed version.
4. Recommend minimal upgrade path; avoid mass major bumps without tests.
5. Flag clearly abandoned deps when easy to see.

## Output
Severity table + recommended actions.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

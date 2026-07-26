---
name: terraform-plan-review
version: 1.0.0
description: >
  Review infrastructure-as-code plans for destroys, public exposure, and IAM blast radius before apply.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - iac
  - devops
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: terraform-plan-review
  official: true
  security_flags: []
---

# infrastructure-as-code Plan Review

## Steps
1. Read plan (no apply).
2. Highlight destroys/replacements and `0.0.0.0/0`.
3. Review IAM for admin-equivalent rights.
4. Confirm remote state + locking.
5. Require explicit human approval for production apply.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

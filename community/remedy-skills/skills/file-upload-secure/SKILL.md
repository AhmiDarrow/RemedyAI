---
name: file-upload-secure
version: 1.0.0
description: >
  Harden file uploads: authz, size/type checks, safe storage keys, download posture.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - security
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: file-upload-secure
  official: true
  security_flags: []
---

# File Upload Secure

## Checklist
1. Authz who can upload/download.
2. Server-side max size; stream to disk/object store.
3. Allowlist types; verify magic bytes when critical.
4. Random object keys; never trust raw filenames as paths.
5. Controlled download headers (`Content-Disposition`).
6. Block executables for untrusted users.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

---
name: k8s-manifest-review
version: 1.0.0
description: >
  Review Kubernetes manifests for probes, resources, securityContext, and rollout safety.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - k8s
  - devops
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: k8s-manifest-review
  official: true
  security_flags: []
---

# K8S Manifest Review

## Checklist
1. requests/limits present.
2. Sensible liveness/readiness.
3. non-root securityContext where possible.
4. Secrets not plaintext in git.
5. RollingUpdate / PDB for critical services.
6. HPA metrics sanity if used.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

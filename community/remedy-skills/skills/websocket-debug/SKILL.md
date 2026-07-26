---
name: websocket-debug
version: 1.0.0
description: >
  Diagnose WebSocket handshake, auth, ping/pong, and reconnect storms.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - network
  - debug
kind: native
status: discovered
tools:
  - file_read
  - bash_exec
metadata:
  source: library
  library_id: websocket-debug
  official: true
  security_flags: []
---

# Websocket Debug

## Steps
1. Confirm ws/wss and proxy idle timeouts.
2. Inspect handshake auth headers/protocols.
3. Server ping cadence; client backoff with jitter.
4. Log close codes.
5. Recommend concrete fix.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.

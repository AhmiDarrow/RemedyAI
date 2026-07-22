---
name: memory-backup
description: Create a dated full backup copy of the memory store. Useful before major changes.
version: 1.0.0
author: Remedy
tags:
  - memory
  - backup
  - utility
requires: []
tools: []
---

# Memory Backup

Creates a timestamped backup of the Remedy memory store, preserving all entries,
handoff notes, and session summaries.

## Instructions

1. Locate the current memory database at `~/.remedy/memory.db`.
2. Create a backup copy named `memory_backup_{date}_{time}.db` in `~/.remedy/backups/`.
3. Verify the backup file exists and has a non-zero size.
4. Report the backup location and file size.

## Safety

- Read-only on the source database.
- Does not modify or delete any data.
- Creates the `backups/` directory if it doesn't exist.

---
name: memory-backup
description: Create a dated full backup of the Remedy memory database before risky changes.
version: 1.0.0
author: Remedy
tags: [memory, backup, utility]
---

# Memory Backup

## When to use
User asks to backup memory, before major migrations, or before destructive cleanup.

## Steps
1. Ensure `~/.remedy/backups/` exists (create if needed).
2. Copy `~/.remedy/memory.db` to `memory_backup_YYYYMMDD_HHMMSS.db`.
3. Confirm the backup exists and size is non-zero.
4. Report the path and size. Do not delete the original.

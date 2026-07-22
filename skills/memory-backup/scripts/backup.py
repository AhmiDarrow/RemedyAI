#!/usr/bin/env python3
"""Backup script for the memory-backup skill."""

import shutil
import sys
from datetime import datetime
from pathlib import Path


def backup_memory(home_dir: Path) -> Path:
    db_path = home_dir / "memory.db"
    if not db_path.exists():
        print(f"Error: No memory database found at {db_path}", file=sys.stderr)
        sys.exit(1)

    backups_dir = home_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backups_dir / f"memory_backup_{timestamp}.db"

    shutil.copy2(str(db_path), str(backup_path))
    size = backup_path.stat().st_size
    print(f"Backup created: {backup_path} ({size} bytes)")
    return backup_path


if __name__ == "__main__":
    home = Path.home() / ".remedy"
    backup_memory(home)

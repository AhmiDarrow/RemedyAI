"""Memory repair and maintenance tools.

Integrity checks, database vacuum, backup, and migration helpers.
Ensures the memory store stays healthy across long-running Remedy sessions.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType


class MemoryRepair:
    """Database maintenance utilities for the Remedy memory store."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def check_integrity(self) -> dict:
        """Run integrity checks and return results."""
        db = self.store._ensure_db()
        result = db.execute("PRAGMA integrity_check").fetchone()
        fts_result = db.execute("PRAGMA quick_check").fetchone()
        wal_size = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        freelist = db.execute("PRAGMA freelist_count").fetchone()[0]

        stats = db.execute(
            "SELECT entry_type, COUNT(*) as cnt FROM memory_entries GROUP BY entry_type"
        ).fetchall()

        return {
            "integrity": result[0] if result else "unknown",
            "quick_check": fts_result[0] if fts_result else "unknown",
            "page_count": page_count,
            "freelist_pages": freelist,
            "estimated_size_bytes": page_count * 4096,
            "entry_counts": {r[0]: r[1] for r in stats},
            "fte_count": db.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0],
            "handoff_count": db.execute("SELECT COUNT(*) FROM handoff_notes").fetchone()[0],
            "session_count": db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0],
        }

    async def vacuum(self) -> dict:
        """Rebuild the database file to reclaim space."""
        db = self.store._ensure_db()
        before_size = self.store.path.stat().st_size if self.store.path.exists() else 0
        db.execute("PRAGMA optimize")
        db.execute("VACUUM")
        db.commit()
        after_size = self.store.path.stat().st_size if self.store.path.exists() else 0
        return {
            "before_bytes": before_size,
            "after_bytes": after_size,
            "reclaimed_bytes": max(0, before_size - after_size),
        }

    async def backup(self, backup_dir: Optional[Path] = None) -> Path:
        """Create a timestamped backup of the memory database.

        Args:
            backup_dir: Optional directory for backups. Defaults to <db_dir>/backups/.

        Returns:
            Path to the backup file.
        """
        db_path = self.store.path
        if not db_path.exists():
            raise FileNotFoundError(f"Memory database not found: {db_path}")

        backup_base = (backup_dir or db_path.parent / "backups")
        backup_base.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_base / f"memory_backup_{timestamp}.db"

        # Safe backup: copy WAL into main DB first, then copy
        await self.checkpoint()
        shutil.copy2(str(db_path), str(backup_path))

        # Also create a manifest
        manifest_path = backup_base / f"memory_backup_{timestamp}.json"
        import json
        info = await self.check_integrity()
        manifest_path.write_text(json.dumps({
            "backup_path": str(backup_path),
            "original_path": str(db_path),
            "timestamp": timestamp,
            "stats": info,
        }, indent=2, default=str))

        return backup_path

    async def checkpoint(self) -> None:
        """Force a WAL checkpoint to flush pending writes."""
        db = self.store._ensure_db()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.commit()

    async def rebuild_fts(self) -> bool:
        """Rebuild the FTS5 index from scratch."""
        db = self.store._ensure_db()
        db.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
        db.commit()
        return True

    async def repair_orphan_references(self) -> int:
        """Clean up entries referencing deleted handoffs or sessions."""
        db = self.store._ensure_db()
        fixed = 0

        # Remove memory entries referencing non-existent handoffs
        valid_handoff_ids = {
            r[0] for r in db.execute("SELECT id FROM handoff_notes").fetchall()
        }
        orphan_entries = db.execute(
            "SELECT id, metadata FROM memory_entries WHERE entry_type = 'handoff'"
        ).fetchall()
        for row in orphan_entries:
            import json
            meta = json.loads(row[1]) if row[1] else {}
            hid = meta.get("handoff_id", "")
            if hid and hid not in valid_handoff_ids:
                db.execute("DELETE FROM memory_entries WHERE id = ?", (row[0],))
                fixed += 1

        db.commit()
        return fixed

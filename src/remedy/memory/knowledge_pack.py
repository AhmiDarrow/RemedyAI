"""Import a folder of notes (Markdown / text) into durable memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.models import MemoryEntry, MemoryEntryType

_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org"}


async def import_knowledge_pack(
    store: Any,
    root: str | Path,
    *,
    max_files: int = 200,
    max_bytes_per_file: int = 200_000,
    tag: str = "knowledge-pack",
) -> dict[str, Any]:
    """Walk *root* and upsert text notes into the memory store.

    Returns counts and any errors (non-fatal per-file).
    """
    path = Path(root).expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()

    if not path.exists():
        return {"ok": False, "error": f"Path not found: {path}", "imported": 0}
    if not path.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}", "imported": 0}

    imported = 0
    skipped = 0
    errors: list[str] = []
    files: list[Path] = []
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        files.append(p)
        if len(files) >= max_files:
            break

    for p in files:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            if len(raw) > max_bytes_per_file:
                raw = raw[:max_bytes_per_file] + "\n…[truncated]"
            if not raw.strip():
                skipped += 1
                continue
            rel = p.relative_to(path).as_posix()
            entry = MemoryEntry(
                title=f"Knowledge: {rel}",
                content=raw,
                entry_type=MemoryEntryType.NOTE,
                tags=[tag, "import"],
                importance=0.65,
                metadata={"source_path": str(p), "pack_root": str(path)},
            )
            await store.upsert(entry)
            imported += 1
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            if len(errors) >= 20:
                break

    return {
        "ok": True,
        "root": str(path),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "scanned": len(files),
    }

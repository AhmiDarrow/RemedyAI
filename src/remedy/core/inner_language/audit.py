"""Always write dense + decoded forms so Joe can audit the channel."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import replace_with_retry, scratch_path

AUDIT_NAME = "inner_language.jsonl"
AUDIT_MAX_BYTES = 256 * 1024
AUDIT_MAX_LINES = 400


def _home(home: str | Path | None) -> Path | None:
    if home:
        return Path(home).expanduser()
    with suppress(Exception):
        from remedy.interfaces.config import get_home_dir

        return Path(get_home_dir())
    return None


def audit_path(home: str | Path | None) -> Path | None:
    root = _home(home)
    return (root / AUDIT_NAME) if root else None


def record_audit(
    home: str | Path | None,
    *,
    kind: str,
    dense: str,
    decoded: str,
    extra: dict[str, Any] | None = None,
) -> None:
    path = audit_path(home)
    if path is None:
        return
    # Audit stays on whenever the language is used; the knob cannot hide rows.
    entry = {
        "ts": time.time(),
        "kind": kind,
        "dense": dense,
        "decoded": decoded,
    }
    if extra:
        entry.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with suppress(Exception):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _rotate(path)


def _rotate(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= AUDIT_MAX_BYTES:
        return
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return
    if len(lines) <= AUDIT_MAX_LINES // 2:
        return
    keep = lines[-AUDIT_MAX_LINES:] if len(lines) > AUDIT_MAX_LINES else lines[len(lines) // 2 :]
    tmp = scratch_path(path)
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    replace_with_retry(tmp, path)


def recent_audit(home: str | Path | None, limit: int = 20) -> list[dict[str, Any]]:
    path = audit_path(home)
    if path is None or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) * 2 :]:
        with suppress(Exception):
            item = json.loads(line)
            if isinstance(item, dict) and "decoded" in item:
                rows.append(item)
    return rows[-limit:]

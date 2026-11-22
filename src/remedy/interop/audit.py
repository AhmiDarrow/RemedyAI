"""Every inbound and outbound peer exchange is written to the audit log."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from remedy.core.atomic_json import replace_with_retry, scratch_path
from remedy.core.organism_knobs import resolve_bool
from remedy.interop.paths import audit_path
from remedy.interop.redact import redact

AUDIT_MAX_BYTES = 512 * 1024
AUDIT_MAX_LINES = 800


def record(
    home: str | None,
    *,
    direction: str,
    protocol: str,
    peer_id: str | None,
    action: str,
    summary: str,
    extra: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    if not resolve_bool("interop_audit", cfg):
        return
    path = audit_path(home)
    entry: dict[str, Any] = {
        "ts": time.time(),
        "direction": direction,
        "protocol": protocol,
        "peer_id": peer_id,
        "action": action,
        "summary": redact(summary) if isinstance(summary, str) else summary,
    }
    if extra:
        entry["extra"] = redact(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with suppress(Exception):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _rotate(path)


def recent(home: str | None, limit: int = 20) -> list[dict[str, Any]]:
    path = audit_path(home)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) * 2 :]:
        with suppress(Exception):
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows[-limit:]


def _rotate(path) -> None:
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

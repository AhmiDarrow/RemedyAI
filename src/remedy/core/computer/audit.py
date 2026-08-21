"""Local audit log for computer-use actions (engineering, not a product gate)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.home import default_home


def _home(home_dir: Path | str | None = None) -> Path:
    if home_dir is not None:
        return Path(home_dir).expanduser()
    return default_home()


def audit_path(home_dir: Path | str | None = None) -> Path:
    root = _home(home_dir) / "computer"
    root.mkdir(parents=True, exist_ok=True)
    return root / "audit.jsonl"


def log_computer_action(
    *,
    action: str,
    target: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
    session_id: str | None = None,
    home_dir: Path | str | None = None,
) -> None:
    try:
        safe_detail: dict[str, Any] = dict(detail or {})
        try:
            from remedy.core.metabolism.redact import redact_obj

            red = redact_obj(safe_detail)
            safe_detail = red if isinstance(red, dict) else {"_redacted": True}
        except Exception:
            # Fail closed: never write unredacted detail if scrub fails
            safe_detail = {}
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "target": target,
            "ok": ok,
            "session_id": session_id,
            "detail": safe_detail,
        }
        path = audit_path(home_dir)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass

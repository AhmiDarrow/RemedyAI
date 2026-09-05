"""Parse computer/navigate tool JSON bodies."""

from __future__ import annotations

import json
from contextlib import suppress


def browse_tool_ok(body: str) -> tuple[bool, bool]:
    """Parse a computer/navigate tool body → (ok_true, ok_false).

    JSON ``ok`` is authoritative. Do not substring-match ``success``
    (matches ``unsuccessful``) or ``user_visible`` (present on failures too).
    """
    raw = body or ""
    low = raw.lower()
    failed = "rail_failed" in low
    with suppress(Exception):
        data = json.loads(raw)
        if isinstance(data, dict) and "ok" in data:
            ok = bool(data["ok"]) and not failed
            return ok, (not bool(data["ok"])) or failed
    import re as _re

    if failed or _re.search(r'"ok"\s*:\s*false', low):
        return False, True
    if _re.search(r'"ok"\s*:\s*true', low):
        return True, False
    return False, False

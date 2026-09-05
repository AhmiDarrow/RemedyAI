"""Shared buffer helpers for host_binding surface modules."""

from __future__ import annotations

import json
from typing import Any

from ._core import _take


def take_json(library: Any, ptr: Any, length: Any) -> Any:
    """Parse a library JSON buffer; ``null`` becomes ``None``."""
    raw = _take(library, ptr, length)
    if not raw or raw == b"null":
        return None
    return json.loads(raw)

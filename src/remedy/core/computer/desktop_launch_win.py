"""Windows launch policy — re-export tip shared ``desktop_launch``.

``desktop_win`` keeps the historical import surface; implementation lives in
the cross-platform launch module (spawn_hidden / fail-closed HostError).
"""

from __future__ import annotations

from remedy.core.computer.desktop_launch import (
    _open_app_is_protocol_or_url,
    is_text_document_path,
    open_app,
    open_url,
    refuse_os_open_text_document,
)

__all__ = [
    "_open_app_is_protocol_or_url",
    "is_text_document_path",
    "open_app",
    "open_url",
    "refuse_os_open_text_document",
]

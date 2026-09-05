"""Session sidebar titles are Go-owned (``native/go/httpapi/titles.go``).

The FastAPI ``routes/sessions/titles`` helpers are gone; coverage lives in
``titles_test.go``.
"""

from __future__ import annotations

import importlib.util


def test_fastapi_session_titles_module_gone() -> None:
    assert importlib.util.find_spec("remedy.interfaces.routes.sessions") is None

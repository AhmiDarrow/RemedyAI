"""Session sidebar titles are Go-owned (``native/go/httpapi/titles.go``).

The FastAPI ``routes/sessions/titles`` helpers are gone; coverage lives in
``titles_test.go``.
"""

from __future__ import annotations

import importlib.util


def test_fastapi_session_titles_module_gone() -> None:
    # Parent package ``remedy.interfaces.routes`` was removed with the FastAPI
    # cutover; find_spec raises ModuleNotFoundError for a missing intermediate
    # rather than returning None.
    try:
        spec = importlib.util.find_spec("remedy.interfaces.routes.sessions")
    except ModuleNotFoundError:
        return
    assert spec is None

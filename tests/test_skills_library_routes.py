"""Skills Library HTTP is Go-owned; keep a TestClient boundary + suggest helpers.

Production ``/api/skills/library/*`` lives in ``native/go/httpapi``
(``skills_library.go`` / ``skills_test.go``). The FastAPI twin — including
leftover Python-only ``update/{id}`` and ``submit`` — is gone. Catalog /
install / search unit coverage stays in ``test_skills_library.py``.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app
from remedy.skills.library.suggest import (
    clear_session_suppress,
    is_suppressed,
    suppress_suggest,
)


def test_skills_library_http_routes_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/skills/library/catalog",
        "/api/skills/library/search",
        "/api/skills/library/suggest",
        "/api/skills/library/suggest/dismiss",
        "/api/skills/library/install",
        "/api/skills/library/updates",
        "/api/skills/library/update/{skill_id}",
        "/api/skills/library/submit",
    ):
        assert path not in paths


def test_dismissing_a_suggestion_suppresses_it_for_that_session() -> None:
    try:
        suppress_suggest("s-9", "alpha")
        assert is_suppressed("s-9", "alpha")
        assert not is_suppressed("other", "alpha")
    finally:
        clear_session_suppress("s-9")


def test_dismissing_without_a_session_id_lands_in_the_default_bucket() -> None:
    try:
        suppress_suggest("_default", "gamma")
        assert is_suppressed("_default", "gamma")
    finally:
        clear_session_suppress("_default")

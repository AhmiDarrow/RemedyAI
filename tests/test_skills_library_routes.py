"""Skills Library suggest helpers; HTTP /api/skills/library/* is Go-owned.

Production lives in ``native/go/httpapi`` (``skills_library.go`` / ``skills_test.go``).
Catalog / install / search unit coverage stays in ``test_skills_library.py``.
"""

from __future__ import annotations

from remedy.skills.library.suggest import (
    clear_session_suppress,
    is_suppressed,
    suppress_suggest,
)


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

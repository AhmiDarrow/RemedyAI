"""A stored macro must never carry a credential to disk.

``observe_chain`` sanitises URLs (userinfo and query string, "tokens often live
there") and screens every argument through ``looks_like_secret_text``. Both ran
inside ``try: … except: pass``, and the line *after* the try stored the value —
so an exception in either left ``val`` at its original, unscreened value and
wrote it into the persisted macro.
"""

from __future__ import annotations

import pytest

from remedy.core.metabolism import cua_macros

SECRET_URL = "https://user:pw@example.com/page?token=SECRET123&x=1"


def _chain():
    return [
        {"tool": "computer_navigate", "args": {"url": SECRET_URL}},
        {"tool": "computer_click", "args": {"ref": "button-1"}},
    ]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    yield


def test_userinfo_and_query_are_stripped_normally():
    assert cua_macros._sanitize_url(SECRET_URL) == "https://example.com/page"


def test_a_normal_chain_stores_only_the_clean_url():
    macro = cua_macros.CuaMacroStore().observe_chain(_chain())
    assert macro is not None
    blob = str([s["args"] for s in macro.steps])
    assert "SECRET123" not in blob
    assert "user:pw" not in blob
    assert "example.com/page" in blob


def test_a_broken_sanitiser_drops_the_argument_rather_than_storing_it(monkeypatch):
    def _boom(_v):
        raise RuntimeError("sanitiser failed")

    monkeypatch.setattr(cua_macros, "_sanitize_url", _boom)
    macro = cua_macros.CuaMacroStore().observe_chain(_chain())
    blob = str([s["args"] for s in (macro.steps if macro else [])])
    assert "SECRET123" not in blob, "the raw URL was written to the stored macro"
    assert "user:pw" not in blob


def test_a_broken_secret_screen_drops_the_argument_too(monkeypatch):
    def _boom(_v):
        raise RuntimeError("screen failed")

    monkeypatch.setattr(cua_macros, "looks_like_secret_text", _boom)
    macro = cua_macros.CuaMacroStore().observe_chain(_chain())
    blob = str([s["args"] for s in (macro.steps if macro else [])])
    assert "SECRET123" not in blob


def test_the_rest_of_the_chain_survives_a_dropped_argument(monkeypatch):
    """Dropping one argument must not throw the whole macro away — a slightly
    less precise macro is the price, not a lost one."""
    monkeypatch.setattr(
        cua_macros, "_sanitize_url", lambda _v: (_ for _ in ()).throw(RuntimeError())
    )
    macro = cua_macros.CuaMacroStore().observe_chain(_chain())
    assert macro is not None
    assert any(s["args"].get("ref") == "button-1" for s in macro.steps)


def test_typed_text_is_never_stored():
    """Whatever was typed could be a password; the macro keeps the shape only."""
    chain = [
        {"tool": "computer_navigate", "args": {"url": "https://example.com"}},
        {"tool": "computer_type", "args": {"text": "hunter2-my-password"}},
    ]
    macro = cua_macros.CuaMacroStore().observe_chain(chain)
    assert macro is not None
    assert "hunter2" not in str([s["args"] for s in macro.steps])

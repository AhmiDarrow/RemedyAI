"""A check that cannot run must not become a check that passed.

Two write/read gates were wrapped in ``try: ... except: pass``. On error they
fell through to the permissive branch, so the failure mode of the guard was
"allow" — which is the one thing a guard must never do.
"""

from __future__ import annotations

import pytest

from remedy.core import security


class TestProtectedSecretPath:
    """``~/.remedy/auth`` sits *under* the home write root, so falling through
    the secret check meant the shell could write the keys the jail exists to
    keep out of tool paths."""

    def test_a_normal_path_is_not_protected(self):
        assert security.is_protected_secret_path_strict("/tmp/notes.txt") is False

    def test_an_auth_path_is_protected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        security.clear_protected_auth_roots_cache()
        assert security.is_protected_secret_path_strict(tmp_path / "auth" / "xai.json")
        security.clear_protected_auth_roots_cache()

    def test_a_check_that_explodes_refuses(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("cannot resolve auth roots")

        monkeypatch.setattr(security, "_resolved_auth_roots", _boom)
        assert security.is_protected_secret_path_strict("/tmp/notes.txt") is True

    def test_none_is_not_protected(self):
        assert security.is_protected_secret_path_strict(None) is False

    def test_the_shell_jail_uses_the_strict_form(self):
        """Named so moving it back to the swallowing form has to be argued."""
        import inspect

        from remedy.core import shell_write_jail

        src = inspect.getsource(shell_write_jail)
        assert "is_protected_secret_path_strict" in src
        assert "except Exception:\n            pass\n        if _under_any" not in src


class TestJailedAttachments:
    """Callers wrap the filter in ``suppress`` and keep their *unfiltered* list
    on error — so one unverifiable path disabled the gate for the whole
    request, which is the forged-path exfiltration it exists to stop."""

    def test_a_path_outside_the_tree_is_dropped(self):
        from remedy.interfaces.attachments import filter_jailed_attachments

        kept = filter_jailed_attachments(
            [{"name": "outside", "path": "/etc/passwd"}]
        )
        assert kept == []

    def test_pathless_metadata_survives(self):
        from remedy.interfaces.attachments import filter_jailed_attachments

        kept = filter_jailed_attachments([{"name": "just a name"}])
        assert [k["name"] for k in kept] == ["just a name"]

    def test_a_check_that_explodes_drops_the_attachment(self, monkeypatch):
        from remedy.interfaces import attachments

        def _boom(*_a, **_kw):
            raise RuntimeError("cannot resolve")

        monkeypatch.setattr(attachments, "is_path_under_attachments", _boom)
        kept = attachments.filter_jailed_attachments(
            [
                {"name": "meta"},
                {"name": "forged", "path": "C:/Windows/System32/config/SAM"},
            ]
        )
        assert [k["name"] for k in kept] == ["meta"]

    def test_the_filter_never_raises(self, monkeypatch):
        """Its callers suppress exceptions, so raising is the same as failing
        open. It has to answer, always."""
        from remedy.interfaces import attachments

        monkeypatch.setattr(
            attachments,
            "is_path_under_attachments",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("nope")),
        )
        assert attachments.filter_jailed_attachments([{"path": "x"}]) == []

    @pytest.mark.parametrize("bad", [None, [], [None], ["not a dict"], [{}]])
    def test_junk_input_is_handled(self, bad):
        from remedy.interfaces.attachments import filter_jailed_attachments

        assert isinstance(filter_jailed_attachments(bad), list)


class TestPathGuardsAreCaseInsensitive:
    """Windows is the primary platform and its filesystem is case-insensitive,
    so ``~/.REMEDY/AUTH/xai.json`` is the same file as ``~/.remedy/auth/xai.json``.
    A guard that only recognises one spelling protects only one spelling.
    """

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        security.clear_protected_auth_roots_cache()
        (tmp_path / "auth").mkdir(parents=True, exist_ok=True)
        yield tmp_path
        security.clear_protected_auth_roots_cache()

    @pytest.mark.parametrize(
        "spelling",
        ["auth/xai.json", "AUTH/xai.json", "Auth/XAI.JSON", "aUtH/deep/nested.json"],
    )
    def test_every_spelling_of_auth_is_protected(self, _home, spelling):
        assert security.is_protected_secret_path(_home / spelling) is True

    def test_a_traversal_that_lands_in_auth_is_protected(self, _home):
        assert security.is_protected_secret_path(
            _home / "auth" / ".." / "auth" / "x.json"
        )

    def test_an_ordinary_file_is_not(self, _home):
        assert security.is_protected_secret_path(_home / "notes.txt") is False

    @pytest.mark.parametrize(
        "spelling",
        [".remedy/auth/x.json", ".REMEDY/AUTH/x.json", ".remedy/undo/s.jsonl"],
    )
    def test_the_restore_guard_matches_the_same_spellings(self, spelling):
        from pathlib import Path

        from remedy.core.time_travel import SessionUndoLog

        assert SessionUndoLog._is_restore_forbidden(Path.home() / spelling) is True


class TestJailedAttachmentCallSites:
    """The three callers imported the filter *inside* ``suppress(Exception)``.
    An import failure was therefore silent and left every attachment in —
    the gate vanished without a log line. The import now lives at module
    level, so a failure is an import error at startup, and a filter that
    explodes at runtime drops the attachments instead of keeping them."""

    @pytest.mark.parametrize(
        "modname",
        [
            "remedy.interfaces.routes.sessions.messages",
            "remedy.interfaces.routes.sessions.stream",
            "remedy.vision.service",
        ],
    )
    def test_the_filter_is_bound_at_module_level(self, modname):
        import importlib
        import inspect

        mod = importlib.import_module(modname)
        assert callable(getattr(mod, "filter_jailed_attachments", None))
        src = inspect.getsource(mod)
        assert "    from remedy.interfaces.attachments import filter_jailed_attachments" not in src

    def test_vision_decode_keeps_no_image_when_the_jail_explodes(self, monkeypatch, tmp_path):
        from remedy.vision import service

        img = tmp_path / "shot.png"
        img.write_bytes(b"not really a png")

        def _boom(*_a, **_kw):
            raise RuntimeError("cannot resolve")

        monkeypatch.setattr(service, "filter_jailed_attachments", _boom)
        monkeypatch.setattr(
            service, "decode_images", lambda *a, **kw: pytest.fail("decoded a jailed image")
        )
        out = service.decode_for_turn(
            [{"name": "shot.png", "path": str(img), "mime": "image/png", "is_image": True}],
            provider="openai",
            model="gpt-4o",
            cfg={"home_dir": str(tmp_path)},
        )
        assert out["mode"] == "text_only"
        assert out["briefs"] == []

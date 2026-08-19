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

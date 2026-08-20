"""The self-updater must never install something the user did not agree to.

`remedy update` can overwrite the running installation: `git pull` + `pip
install -e .`, or `pip install --upgrade remedy-ai`. If its guards are wrong the
damage is silent and permanent — it upgrades on `--check`, it upgrades with no
tty to ask on, it upgrades after the user answered "no", or it pulls over a
dirty checkout and destroys a local self-improve draft that was never shipped.
The other half is honesty: an unreachable PyPI must not read as "up to date",
and a directory that merely looks like a checkout must not be mistaken for this
project's source tree.

These tests never touch the network, the real home, or a real git repo — every
subprocess and every HTTP call is a fake.
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
import urllib.request
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import remedy
from remedy.core import self_inject_draft
from remedy.interfaces import updater as upd


class _Result:
    """Stand-in for the CompletedProcess that run_hidden returns."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Recorder:
    """Fake run_hidden: records argv/kwargs, replays queued results."""

    def __init__(self, *results: _Result):
        self.calls: list[tuple[list[str], dict]] = []
        self._results = list(results)

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if self._results:
            return self._results.pop(0)
        return _Result()

    @property
    def argvs(self) -> list[list[str]]:
        return [c for c, _ in self.calls]


class _Stdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def no_install(monkeypatch):
    """Trip-wire: any real install attempt during a test fails loudly."""
    tripped: list[str] = []

    def boom_git(_root):
        tripped.append("git")
        return True

    def boom_pip():
        tripped.append("pip")
        return True

    monkeypatch.setattr(upd, "_git_pull_and_reinstall", boom_git)
    monkeypatch.setattr(upd, "_pip_upgrade", boom_pip)
    return tripped


def _stub_environment(
    monkeypatch,
    *,
    installed: str = "1.0.0",
    latest: str | None = "1.0.0",
    source: str = "pip",
    root: Path | None = None,
    tty: bool = True,
    confirm: bool = True,
):
    monkeypatch.setattr(upd, "_get_installed_version", lambda: installed)
    monkeypatch.setattr(upd, "_get_latest_version", lambda: latest)
    monkeypatch.setattr(upd, "_detect_install_source", lambda: source)
    monkeypatch.setattr(upd, "_find_project_root", lambda: root)
    monkeypatch.setattr(upd.sys, "stdin", _Stdin(tty))
    monkeypatch.setattr(upd.Confirm, "ask", lambda *_a, **_k: confirm)
    # No git subprocess unless a test explicitly wants one.
    monkeypatch.setattr(shutil, "which", lambda _name: None)


# --------------------------------------------------------------------------
# version parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("V1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("2", (2, 0, 0)),
        ("1.2.3.4", (1, 2, 3, 4)),
        ("1.2.3-rc1", (1, 2, 3)),
        ("1.2.3+build.7", (1, 2, 3)),
        ("  1.2.3  ", (1, 2, 3)),
        ("0.10.26", (0, 10, 26)),
    ],
)
def test_a_version_string_parses_into_a_comparable_tuple(raw, expected):
    assert upd._parse_version(raw) == expected


@pytest.mark.parametrize("junk", ["", "   ", "abc", None, "v", "not.a.version"])
def test_unparseable_versions_degrade_to_zeros_instead_of_raising(junk):
    """A garbage version must never crash the updater; it just sorts lowest."""
    assert upd._parse_version(junk) == (0, 0, 0)


def test_empty_dot_separated_segments_each_count_as_a_zero_component():
    """"..." yields four zero components, so it sorts *above* a plain (0, 0, 0)."""
    assert upd._parse_version("...") == (0, 0, 0, 0)
    assert upd._is_newer("...", "abc") is True


def test_a_ten_sorts_above_a_nine_rather_than_lexically():
    assert upd._parse_version("0.10.0") > upd._parse_version("0.9.9")
    assert upd._is_newer("0.10.0", "0.9.9")


@pytest.mark.parametrize(
    ("latest", "installed", "newer"),
    [
        ("1.0.1", "1.0.0", True),
        ("1.0.0", "1.0.0", False),
        ("0.9.9", "1.0.0", False),
        ("1.0.0", "unknown", True),
        ("unknown", "1.0.0", False),
    ],
)
def test_is_newer_is_strict_and_never_upgrades_sideways(latest, installed, newer):
    assert upd._is_newer(latest, installed) is newer


@pytest.mark.parametrize(
    "raw", ["0.19.0rc1", "0.19.0b2", "0.19.0a1", "0.19.0-rc1", "0.19.0+build7"]
)
def test_a_prerelease_never_reads_as_newer_than_its_release(raw):
    """Only ``-``/``+`` suffixes were stripped, and every digit in a segment was
    joined — so "0.19.0rc1" became (0, 19, 1) and `remedy update` offered a
    release candidate as an upgrade from the finished release."""
    assert upd._parse_version(raw) == (0, 19, 0)
    assert upd._is_newer(raw, "0.19.0") is False


def test_a_real_upgrade_is_still_recognised():
    """The guard must not have made every comparison say no."""
    assert upd._is_newer("0.19.1", "0.19.0") is True
    assert upd._is_newer("0.20.0", "0.19.9") is True
    assert upd._is_newer("0.19.10", "0.19.9") is True


# --------------------------------------------------------------------------
# installed-version resolution
# --------------------------------------------------------------------------


def test_the_source_tree_version_wins_over_a_stale_dist_info(monkeypatch):
    monkeypatch.setattr(remedy, "__version__", "9.9.9")
    monkeypatch.setattr(upd, "_distribution_version", lambda: "1.0.0")
    assert upd._get_installed_version() == "9.9.9"


@pytest.mark.parametrize("placeholder", ["0.0.0", "", None])
def test_a_placeholder_package_version_falls_back_to_dist_metadata(monkeypatch, placeholder):
    monkeypatch.setattr(remedy, "__version__", placeholder)
    monkeypatch.setattr(upd, "_distribution_version", lambda: "7.7.7")
    assert upd._get_installed_version() == "7.7.7"


def test_a_missing_package_version_attribute_is_not_an_error(monkeypatch):
    monkeypatch.delattr(remedy, "__version__")
    monkeypatch.setattr(upd, "_distribution_version", lambda: "2.0.0")
    assert upd._get_installed_version() == "2.0.0"


def test_with_no_version_anywhere_the_answer_is_unknown_not_a_crash(monkeypatch):
    monkeypatch.setattr(remedy, "__version__", "0.0.0")
    monkeypatch.setattr(upd, "_distribution_version", lambda: None)
    assert upd._get_installed_version() == "unknown"


def test_the_remedy_ai_dist_is_consulted_before_the_legacy_remedy_name(monkeypatch):
    asked: list[str] = []

    def fake_version(name: str) -> str:
        asked.append(name)
        if name == "remedy-ai":
            raise PackageNotFoundError(name)
        return "3.2.1"

    monkeypatch.setattr(upd, "importlib_version", fake_version)
    assert upd._distribution_version() == "3.2.1"
    assert asked == ["remedy-ai", "remedy"]


def test_a_broken_metadata_backend_yields_none_rather_than_propagating(monkeypatch):
    def fake_version(_name: str) -> str:
        raise RuntimeError("metadata store corrupt")

    monkeypatch.setattr(upd, "importlib_version", fake_version)
    assert upd._distribution_version() is None


def test_get_distribution_returns_none_when_no_candidate_name_resolves(monkeypatch):
    asked: list[str] = []

    def fake_distribution(name: str):
        asked.append(name)
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", fake_distribution)
    assert upd._get_distribution() is None
    assert asked == ["remedy-ai", "remedy"]


# --------------------------------------------------------------------------
# PyPI lookup
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._payload


def test_the_pypi_probe_asks_for_remedy_ai_with_a_timeout(monkeypatch):
    seen: dict = {}

    def fake_urlopen(req, **kwargs):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["timeout"] = kwargs.get("timeout")
        return _Resp(json.dumps({"info": {"version": "4.5.6"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert upd._get_latest_version() == "4.5.6"
    assert seen["url"] == upd.PYPI_URL
    assert "remedy-ai" in seen["url"]
    # An unbounded urlopen would hang `remedy update` forever on a dead network.
    assert seen["timeout"] == 10
    assert any("Remedy" in v for v in seen["headers"].values())


@pytest.mark.parametrize(
    "payload",
    [b"not json at all", b"{}", b'{"info": {}}', b"null", b'{"info": null}'],
)
def test_a_malformed_pypi_answer_is_reported_as_unknown_not_as_a_version(monkeypatch, payload):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    assert upd._get_latest_version() is None


def test_a_network_failure_returns_none_instead_of_raising(monkeypatch):
    def fake_urlopen(*_a, **_k):
        raise OSError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert upd._get_latest_version() is None


# --------------------------------------------------------------------------
# install-source detection
# --------------------------------------------------------------------------


class _Dist:
    def __init__(self, text):
        self._text = text

    def read_text(self, _name: str):
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (None, "pip"),
        ("", "pip"),
        (json.dumps({"url": "https://files.pythonhosted.org/x.whl"}), "pip"),
        (
            json.dumps(
                {"url": "https://github.com/AhmiDarrow/RemedyAI.git",
                 "dir_info": {"editable": True}}
            ),
            "git-editable",
        ),
        (json.dumps({"url": "https://github.com/AhmiDarrow/RemedyAI.git"}), "git-folder"),
        (
            json.dumps({"url": "https://github.com/AhmiDarrow/RemedyAI.git",
                        "dir_info": {"editable": False}}),
            "git-folder",
        ),
        ("{ this is not json", "unknown"),
    ],
)
def test_install_source_is_derived_from_direct_url_metadata(monkeypatch, direct_url, expected):
    monkeypatch.setattr(upd, "_get_distribution", lambda: _Dist(direct_url))
    assert upd._detect_install_source() == expected


def test_no_distribution_metadata_means_unknown_not_a_guess(monkeypatch):
    monkeypatch.setattr(upd, "_get_distribution", lambda: None)
    assert upd._detect_install_source() == "unknown"


def test_metadata_that_blows_up_is_reported_as_unknown(monkeypatch):
    monkeypatch.setattr(upd, "_get_distribution", lambda: _Dist(OSError("unreadable")))
    assert upd._detect_install_source() == "unknown"


def test_a_local_editable_checkout_without_git_in_its_url_reads_as_pip(monkeypatch):
    """Documents current behaviour: detection is a substring test on the URL.

    A plain `pip install -e C:/proj/Remedy` records a file:// URL, so the
    editable checkout is classified 'pip' and `remedy update` would try to
    upgrade it from PyPI rather than pulling. Conversely any path containing
    the letters 'git' is treated as a git install.
    """
    editable_local = json.dumps(
        {"url": "file:///C:/proj/Remedy", "dir_info": {"editable": True}}
    )
    monkeypatch.setattr(upd, "_get_distribution", lambda: _Dist(editable_local))
    assert upd._detect_install_source() == "pip"

    misleading = json.dumps({"url": "file:///C:/gitlab-mirror/thing"})
    monkeypatch.setattr(upd, "_get_distribution", lambda: _Dist(misleading))
    assert upd._detect_install_source() == "git-folder"


# --------------------------------------------------------------------------
# project-root discovery
# --------------------------------------------------------------------------


def _fake_checkout(tmp_path: Path, pyproject: str | None, *, git: bool, layout: bool) -> Path:
    root = tmp_path / "proj"
    pkg = root / "src" / "remedy"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    if layout:
        (pkg / "interfaces").mkdir()
        (pkg / "interfaces" / "updater.py").write_text("", encoding="utf-8")
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if git:
        (root / ".git").mkdir()
    return root


def test_a_checkout_whose_pyproject_names_remedy_ai_is_the_project_root(monkeypatch, tmp_path):
    root = _fake_checkout(
        tmp_path, '[project]\nname = "remedy-ai"\n', git=True, layout=True
    )
    monkeypatch.setattr(remedy, "__file__", str(root / "src" / "remedy" / "__init__.py"))
    assert upd._find_project_root() == root.resolve()


def test_a_stranger_project_tree_is_refused_as_the_update_root(monkeypatch, tmp_path):
    """Someone else's pyproject.toml must not be `git pull`ed and reinstalled."""
    root = _fake_checkout(
        tmp_path,
        '[project]\nname = "flask"\ndescription = "a web framework"\n',
        git=False,
        layout=False,
    )
    monkeypatch.setattr(remedy, "__file__", str(root / "src" / "remedy" / "__init__.py"))
    assert upd._find_project_root() != root.resolve()


def test_a_git_tree_with_our_package_layout_is_accepted_without_a_pyproject(
    monkeypatch, tmp_path
):
    root = _fake_checkout(tmp_path, None, git=True, layout=True)
    monkeypatch.setattr(remedy, "__file__", str(root / "src" / "remedy" / "__init__.py"))
    assert upd._find_project_root() == root.resolve()


def test_a_git_tree_missing_our_updater_module_is_not_accepted(monkeypatch, tmp_path):
    root = _fake_checkout(tmp_path, None, git=True, layout=False)
    monkeypatch.setattr(remedy, "__file__", str(root / "src" / "remedy" / "__init__.py"))
    assert upd._find_project_root() != root.resolve()


def test_a_site_packages_install_has_no_project_root(monkeypatch, tmp_path):
    pkg = tmp_path / "site-packages" / "remedy"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(remedy, "__file__", str(pkg / "__init__.py"))
    assert upd._find_project_root() is None


@pytest.mark.parametrize(
    ("text", "ours"),
    [
        ('name = "remedy-ai"', True),
        ("name = 'remedy-ai'", True),
        ('name = "remedy"\ndescription = "a coding agent"', True),
        ('name = "remedy"\nauthors = ["Ahmi"]', True),
        ('name = "remedy"\ndescription = "legacy jinja templating"', False),
        ('name = "flask"', False),
        ("", False),
        ('name="remedy-ai"', False),
    ],
)
def test_only_this_products_pyproject_is_recognised(text, ours):
    assert upd._is_remedy_pyproject(text) is ours


# --------------------------------------------------------------------------
# git pull + reinstall
# --------------------------------------------------------------------------


def test_without_git_on_the_path_the_pull_is_refused_and_nothing_runs(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(upd, "run_hidden", rec)
    assert upd._git_pull_and_reinstall(tmp_path) is False
    assert rec.calls == []


def test_a_successful_pull_reinstalls_editable_in_the_project_root(monkeypatch, tmp_path):
    rec = _Recorder(_Result(0, "Already up to date."), _Result(0, "ok"))
    monkeypatch.setattr(shutil, "which", lambda _n: "C:/git.exe")
    monkeypatch.setattr(upd, "run_hidden", rec)
    assert upd._git_pull_and_reinstall(tmp_path) is True
    assert rec.argvs[0] == ["C:/git.exe", "pull"]
    assert rec.argvs[1] == [sys.executable, "-m", "pip", "install", "-e", "."]
    # Both must run inside the checkout, never in the caller's cwd.
    assert all(kwargs["cwd"] == tmp_path for _cmd, kwargs in rec.calls)
    assert all(kwargs["timeout"] for _cmd, kwargs in rec.calls)


def test_a_failed_pull_does_not_go_on_to_reinstall(monkeypatch, tmp_path):
    rec = _Recorder(_Result(1, "", "merge conflict"))
    monkeypatch.setattr(shutil, "which", lambda _n: "git")
    monkeypatch.setattr(upd, "run_hidden", rec)
    assert upd._git_pull_and_reinstall(tmp_path) is False
    assert len(rec.calls) == 1


def test_a_failed_reinstall_is_reported_as_failure(monkeypatch, tmp_path):
    rec = _Recorder(_Result(0, "updated"), _Result(1, "", "wheel build failed"))
    monkeypatch.setattr(shutil, "which", lambda _n: "git")
    monkeypatch.setattr(upd, "run_hidden", rec)
    assert upd._git_pull_and_reinstall(tmp_path) is False


@pytest.mark.parametrize("failing_step", [0, 1])
def test_a_subprocess_that_raises_is_caught_and_reported_as_failure(
    monkeypatch, tmp_path, failing_step
):
    calls = {"n": 0}

    def exploding(cmd, **_kwargs):
        i = calls["n"]
        calls["n"] += 1
        if i == failing_step:
            raise TimeoutError("child hung")
        return _Result(0, "ok")

    monkeypatch.setattr(shutil, "which", lambda _n: "git")
    monkeypatch.setattr(upd, "run_hidden", exploding)
    assert upd._git_pull_and_reinstall(tmp_path) is False


def test_the_pip_upgrade_path_reports_failure_instead_of_raising(monkeypatch):
    def exploding(*_a, **_k):
        raise OSError("pip missing")

    monkeypatch.setattr(upd, "run_hidden", exploding)
    assert upd._pip_upgrade() is False


# --------------------------------------------------------------------------
# post-update verification
# --------------------------------------------------------------------------


def test_a_failing_post_update_check_is_reported_not_raised(monkeypatch, capsys):
    def boom() -> bool:
        raise RuntimeError("no db")

    monkeypatch.setattr(upd, "_memory_check", boom)
    monkeypatch.setattr(upd, "_config_check", lambda: False)
    monkeypatch.setattr(upd, "_version_check", lambda: True)
    upd._run_post_update_checks()
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "WARN" in out
    assert "OK" in out


def test_the_config_check_probes_the_default_home_and_never_writes(monkeypatch):
    """It stats ~/.remedy/config.toml directly — REMEDY_HOME is not consulted."""
    seen: list[str] = []

    class FakePath:
        def __init__(self, raw):
            seen.append(str(raw))

        def expanduser(self):
            return self

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(upd, "Path", FakePath)
    assert upd._config_check() is False
    assert seen == ["~/.remedy/config.toml"]


def test_the_version_check_is_true_for_any_real_version_string():
    assert upd._version_check() is True


# --------------------------------------------------------------------------
# run_update: what must NOT happen
# --------------------------------------------------------------------------


def test_check_only_never_installs_anything(monkeypatch, capsys, no_install):
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0")
    upd.run_update(check_only=True)
    assert no_install == []
    assert "without --check" in capsys.readouterr().out


def test_an_up_to_date_install_does_nothing_and_does_not_exit_nonzero(
    monkeypatch, capsys, no_install
):
    _stub_environment(monkeypatch, installed="1.0.0", latest="1.0.0")
    assert upd.run_update(check_only=False) is None
    assert no_install == []
    assert "up to date" in capsys.readouterr().out.lower()


def test_a_locally_newer_build_is_not_downgraded_to_the_pypi_version(
    monkeypatch, capsys, no_install
):
    _stub_environment(monkeypatch, installed="9.0.0", latest="1.0.0")
    upd.run_update(check_only=False)
    assert no_install == []
    out = capsys.readouterr().out
    assert "Update available" not in out


def test_an_unreachable_pypi_exits_nonzero_rather_than_claiming_up_to_date(
    monkeypatch, capsys, no_install
):
    _stub_environment(monkeypatch, latest=None)
    with pytest.raises(SystemExit) as ei:
        upd.run_update(check_only=True)
    assert ei.value.code == 1
    assert no_install == []
    assert "up to date" not in capsys.readouterr().out.lower()


@pytest.mark.parametrize("stdin", [None, "not-a-tty"])
def test_without_a_tty_the_update_is_declined_rather_than_applied(
    monkeypatch, capsys, no_install, stdin
):
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0")
    monkeypatch.setattr(
        upd.sys, "stdin", None if stdin is None else _Stdin(False)
    )
    upd.run_update(check_only=False)
    assert no_install == []
    assert "Non-interactive" in capsys.readouterr().out


def test_answering_no_at_the_prompt_cancels_the_update(monkeypatch, capsys, no_install):
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0", confirm=False)
    upd.run_update(check_only=False)
    assert no_install == []
    assert "cancelled" in capsys.readouterr().out.lower()


def test_a_dirty_self_improve_tree_aborts_the_update_instead_of_pulling(
    monkeypatch, capsys, no_install, tmp_path
):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="2.0.0", source="git-editable", root=tmp_path
    )
    monkeypatch.setattr(
        self_inject_draft,
        "origin_wins_if_dirty",
        lambda _root: {"action": "abort_dirty", "reason": "local_self_improve_or_wip"},
    )
    with pytest.raises(SystemExit) as ei:
        upd.run_update(check_only=False)
    assert ei.value.code == 1
    assert no_install == []
    assert "dirty" in capsys.readouterr().out.lower()


def test_a_broken_dirty_check_does_not_silently_block_the_update(
    monkeypatch, tmp_path
):
    """The policy call is best-effort: if it explodes the pull still happens."""
    pulled: list[Path] = []

    def boom(_root):
        raise RuntimeError("git unavailable")

    _stub_environment(
        monkeypatch, installed="1.0.0", latest="2.0.0", source="git-editable", root=tmp_path
    )
    monkeypatch.setattr(self_inject_draft, "origin_wins_if_dirty", boom)
    monkeypatch.setattr(
        upd, "_git_pull_and_reinstall", lambda root: (pulled.append(root), True)[1]
    )
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: None)
    upd.run_update(check_only=False)
    assert pulled == [tmp_path]


# --------------------------------------------------------------------------
# run_update: which install path is chosen
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["git-editable", "git-folder"])
def test_a_git_install_updates_by_pulling_not_by_pip(monkeypatch, tmp_path, source):
    chosen: list[str] = []
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="2.0.0", source=source, root=tmp_path
    )
    monkeypatch.setattr(self_inject_draft, "origin_wins_if_dirty", lambda _r: {"action": "pull"})
    monkeypatch.setattr(
        upd, "_git_pull_and_reinstall", lambda _r: (chosen.append("git"), True)[1]
    )
    monkeypatch.setattr(upd, "_pip_upgrade", lambda: (chosen.append("pip"), True)[1])
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: None)
    upd.run_update(check_only=False)
    assert chosen == ["git"]


@pytest.mark.parametrize("source", ["pip", "unknown"])
def test_a_non_git_install_upgrades_via_pip(monkeypatch, source):
    chosen: list[str] = []
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0", source=source)
    monkeypatch.setattr(
        upd, "_git_pull_and_reinstall", lambda _r: (chosen.append("git"), True)[1]
    )
    monkeypatch.setattr(upd, "_pip_upgrade", lambda: (chosen.append("pip"), True)[1])
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: None)
    upd.run_update(check_only=False)
    assert chosen == ["pip"]


def test_a_git_source_without_a_located_root_falls_back_to_pip(monkeypatch):
    chosen: list[str] = []
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="2.0.0", source="git-editable", root=None
    )
    monkeypatch.setattr(
        upd, "_git_pull_and_reinstall", lambda _r: (chosen.append("git"), True)[1]
    )
    monkeypatch.setattr(upd, "_pip_upgrade", lambda: (chosen.append("pip"), True)[1])
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: None)
    upd.run_update(check_only=False)
    assert chosen == ["pip"]


def test_a_failed_apply_exits_nonzero_and_prints_manual_instructions(monkeypatch, capsys):
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0")
    monkeypatch.setattr(upd, "_pip_upgrade", lambda: False)
    with pytest.raises(SystemExit) as ei:
        upd.run_update(check_only=False)
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "RemedyAI" in out
    assert "Update failed" in out


def test_a_successful_apply_runs_the_post_update_verification(monkeypatch, capsys):
    ran: list[str] = []
    _stub_environment(monkeypatch, installed="1.0.0", latest="2.0.0")
    monkeypatch.setattr(upd, "_pip_upgrade", lambda: True)
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: ran.append("checks"))
    upd.run_update(check_only=False)
    assert ran == ["checks"]
    assert "Update complete" in capsys.readouterr().out


# --------------------------------------------------------------------------
# run_update: git-behind detection
# --------------------------------------------------------------------------


def _git_env(monkeypatch, rev_list: list[_Result]) -> _Recorder:
    rec = _Recorder(_Result(0, ""), *rev_list)
    monkeypatch.setattr(shutil, "which", lambda _n: "git")
    monkeypatch.setattr(upd, "run_hidden", rec)
    return rec


def test_commits_behind_upstream_make_an_update_available_even_when_offline(
    monkeypatch, capsys, tmp_path
):
    chosen: list[str] = []
    _stub_environment(
        monkeypatch, installed="1.0.0", latest=None, source="git-editable", root=tmp_path
    )
    _git_env(monkeypatch, [_Result(0, "3\n")])
    monkeypatch.setattr(self_inject_draft, "origin_wins_if_dirty", lambda _r: {"action": "pull"})
    monkeypatch.setattr(
        upd, "_git_pull_and_reinstall", lambda _r: (chosen.append("git"), True)[1]
    )
    monkeypatch.setattr(upd, "_run_post_update_checks", lambda: None)
    upd.run_update(check_only=False)
    assert chosen == ["git"]
    assert "behind" in capsys.readouterr().out.lower()


def test_zero_commits_behind_is_reported_as_up_to_date(monkeypatch, capsys, tmp_path, no_install):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="1.0.0", source="git-editable", root=tmp_path
    )
    rec = _git_env(monkeypatch, [_Result(0, "0\n")])
    upd.run_update(check_only=False)
    assert no_install == []
    assert "up to date" in capsys.readouterr().out.lower()
    # @{upstream} answered, so the master/main fallbacks are not probed.
    assert len(rec.argvs) == 2
    assert rec.argvs[0][1:] == ["fetch", "origin"]
    assert "HEAD..@{upstream}" in rec.argvs[1]


def test_a_detached_branch_falls_back_to_origin_master_then_origin_main(
    monkeypatch, tmp_path, no_install
):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="1.0.0", source="git-editable", root=tmp_path
    )
    rec = _git_env(
        monkeypatch,
        [
            _Result(128, "", "no upstream configured"),
            _Result(128, "", "unknown revision origin/master"),
            _Result(0, "2\n"),
        ],
    )
    upd.run_update(check_only=True)
    refs = [c[-2] for c in rec.argvs[1:]]
    assert refs == ["HEAD..@{upstream}", "HEAD..origin/master", "HEAD..origin/main"]
    assert no_install == []


def test_git_output_that_is_not_a_count_is_ignored_rather_than_trusted(
    monkeypatch, capsys, tmp_path, no_install
):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="1.0.0", source="git-editable", root=tmp_path
    )
    _git_env(monkeypatch, [_Result(0, "fatal: bad revision"), _Result(0, ""), _Result(0, "  ")])
    upd.run_update(check_only=False)
    assert no_install == []
    assert "up to date" in capsys.readouterr().out.lower()


def test_a_git_probe_that_explodes_does_not_abort_the_update_check(
    monkeypatch, tmp_path, no_install
):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="1.0.0", source="git-editable", root=tmp_path
    )

    def exploding(*_a, **_k):
        raise TimeoutError("git fetch hung")

    monkeypatch.setattr(shutil, "which", lambda _n: "git")
    monkeypatch.setattr(upd, "run_hidden", exploding)
    upd.run_update(check_only=False)
    assert no_install == []


def test_a_pip_install_never_shells_out_to_git_for_a_behind_count(
    monkeypatch, tmp_path, no_install
):
    _stub_environment(
        monkeypatch, installed="1.0.0", latest="1.0.0", source="pip", root=tmp_path
    )
    rec = _git_env(monkeypatch, [_Result(0, "5\n")])
    upd.run_update(check_only=False)
    assert rec.calls == []
    assert no_install == []

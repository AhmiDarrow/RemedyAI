"""CLI self-update must target remedy-ai (not the unrelated PyPI 'remedy')."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.interfaces import updater as upd

ROOT = Path(__file__).resolve().parents[1]


def test_pypi_url_is_remedy_ai_not_unrelated_remedy():
    assert "remedy-ai" in upd.PYPI_URL
    assert upd.PYPI_URL.rstrip("/").endswith("/remedy-ai/json")
    # Occupied unrelated package must never be the check target
    assert "/pypi/remedy/json" not in upd.PYPI_URL
    assert upd.PYPI_DIST_NAME == "remedy-ai"


def test_parse_version_and_is_newer():
    assert upd._parse_version("0.19.0") > upd._parse_version("0.18.9")  # noqa: SLF001
    assert upd._is_newer("0.19.1", "0.19.0")  # noqa: SLF001
    assert not upd._is_newer("0.19.0", "0.19.0")  # noqa: SLF001
    assert not upd._is_newer("0.18.0", "0.19.0")  # noqa: SLF001
    assert upd._parse_version("v1.2.3-rc1") == (1, 2, 3)  # noqa: SLF001


def test_is_remedy_pyproject_accepts_remedy_ai(tmp_path: Path):
    good = tmp_path / "pyproject.toml"
    good.write_text(
        '[project]\nname = "remedy-ai"\nversion = "0.19.0"\n',
        encoding="utf-8",
    )
    assert upd._is_remedy_pyproject(good.read_text(encoding="utf-8"))  # noqa: SLF001

    unrelated = 'name = "remedy"\nversion = "0.0.6"\ndescription = "legacy jinja"\n'
    assert not upd._is_remedy_pyproject(unrelated)  # noqa: SLF001


def test_find_project_root_locates_checkout():
    """Editable install from this monorepo must resolve to repo root."""
    root = upd._find_project_root()  # noqa: SLF001
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "remedy-ai"' in text
    assert (root / "src" / "remedy" / "interfaces" / "updater.py").is_file()
    # Sanity: we found *this* repo, not a random parent
    assert root.resolve() == ROOT.resolve() or (ROOT / "src" / "remedy").is_dir()


def test_get_installed_version_returns_semver_like():
    ver = upd._get_installed_version()  # noqa: SLF001
    assert ver and ver != "unknown"
    parts = upd._parse_version(ver)  # noqa: SLF001
    assert parts[0] >= 0
    # Prefer package __version__ (source tree) over stale site-packages dist-info
    from remedy import __version__ as pkg_ver

    assert ver == pkg_ver or upd._parse_version(ver) == upd._parse_version(pkg_ver)  # noqa: SLF001


def test_manual_clone_hint_points_at_remedyai_repo():
    """Failure help must not send users to the old AhmiDarrow/Remedy URL."""
    src = Path(upd.__file__).read_text(encoding="utf-8")
    assert "AhmiDarrow/RemedyAI" in src
    assert "AhmiDarrow/Remedy.git" not in src
    assert "remedy-ai" in src


def test_pip_upgrade_invokes_remedy_ai(monkeypatch):
    calls: list[list[str]] = []

    class Fake:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return Fake()

    monkeypatch.setattr(upd, "run_hidden", fake_run)
    assert upd._pip_upgrade() is True  # noqa: SLF001
    assert calls
    assert "remedy-ai" in calls[0]
    assert "--upgrade" in calls[0]


def test_distribution_helpers_prefer_remedy_ai():
    # Live env: either dist is present or we still get a non-empty version via __version__
    ver = upd._distribution_version()  # noqa: SLF001
    if ver is None:
        pytest.skip("no dist-info in this env")
    assert isinstance(ver, str) and ver

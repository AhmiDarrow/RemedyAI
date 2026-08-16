"""New Project is first-run only; sessions without a project are root."""

from pathlib import Path

from remedy.core.workspace import (
    default_project_from_config,
    ensure_new_project_seed,
    is_unset_project_path,
    new_project_dir,
    resolve_project_path,
)


def test_new_project_dir_under_documents_or_remedy():
    p = new_project_dir()
    assert "New Project" in str(p)
    assert "Remedy" in str(p) or ".remedy" in str(p)


def test_ensure_new_project_seed_creates(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = ensure_new_project_seed()
    assert p.is_dir()
    assert p.name == "New Project"


def test_default_project_from_config_unset_is_home_not_new_project(
    tmp_path, monkeypatch
):
    """Unset config must not force New Project on every call — home / root."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = default_project_from_config({})
    assert p == resolve_project_path(None)
    assert p == tmp_path.resolve()
    assert is_unset_project_path(None)
    assert is_unset_project_path("")
    assert is_unset_project_path(".")
    assert is_unset_project_path("C:\\")


def test_create_default_config_seeds_new_project_once(tmp_path, monkeypatch):
    """First-run config init may record New Project; not used as every-session lock."""
    from remedy.interfaces.config import create_default_config

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / ".remedy"
    path = create_default_config(home)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "project_path" in text
    # Second call must not rewrite
    mtime = path.stat().st_mtime
    path2 = create_default_config(home)
    assert path2 == path
    assert path.stat().st_mtime == mtime

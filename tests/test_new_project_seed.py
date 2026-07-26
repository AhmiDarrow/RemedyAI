"""New Project sandbox seed."""
from pathlib import Path

from remedy.core.workspace import (
    default_project_from_config,
    ensure_new_project_seed,
    is_unset_project_path,
    new_project_dir,
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


def test_default_project_from_config_seeds_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = default_project_from_config({})
    assert p.is_dir()
    assert is_unset_project_path(None)

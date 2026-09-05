"""RMDY worker must never jail to the Desktop install folder."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime import rmdy_tool_worker as w


def test_looks_like_install_dir(tmp_path: Path) -> None:
    (tmp_path / "remedy-runtime.exe").write_text("x", encoding="utf-8")
    (tmp_path / "webui").mkdir()
    (tmp_path / "windows").mkdir()
    assert w._looks_like_install_dir(tmp_path)


def test_workspace_root_prefers_config_over_install_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "remedy-runtime.exe").write_text("x", encoding="utf-8")
    (install / "webui").mkdir()
    (install / "windows").mkdir()

    home = tmp_path / "home"
    home.mkdir()
    proj = home / "proj"
    proj.mkdir()
    (home / "config.toml").write_text(
        f'project_path = "{proj.as_posix()}"\n', encoding="utf-8"
    )

    monkeypatch.delenv("REMEDY_WORKSPACE", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT_PATH", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT", raising=False)
    monkeypatch.delenv("REMEDY_FILES_ROOT", raising=False)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    monkeypatch.chdir(install)

    assert w._workspace_root() == proj.resolve()
    assert w._workspace_root({"workspace_root": str(proj)}) == proj.resolve()


def test_workspace_root_defaults_to_documents_remedy(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "remedy-runtime.exe").write_text("x", encoding="utf-8")
    (install / "webui").mkdir()
    (install / "windows").mkdir()

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("REMEDY_WORKSPACE", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT_PATH", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT", raising=False)
    monkeypatch.delenv("REMEDY_FILES_ROOT", raising=False)
    monkeypatch.delenv("REMEDY_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(install)

    got = w._workspace_root()
    assert got == (fake_home / "Documents" / "Remedy").resolve()


def test_junk_listing_name_filters_private_use() -> None:
    assert w._is_junk_listing_name("C\uf03a")
    assert not w._is_junk_listing_name("src")


def test_inp_and_env_user_home_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "remedy-runtime.exe").write_text("x", encoding="utf-8")
    (install / "webui").mkdir()
    (install / "windows").mkdir()

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    docs = fake_home / "Documents" / "Remedy"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.delenv("REMEDY_WORKSPACE", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT_PATH", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT", raising=False)
    monkeypatch.delenv("REMEDY_FILES_ROOT", raising=False)
    monkeypatch.delenv("REMEDY_HOME", raising=False)
    monkeypatch.chdir(install)

    assert w._workspace_root({"project_path": str(fake_home)}) == docs.resolve()
    monkeypatch.setenv("REMEDY_WORKSPACE", str(fake_home))
    assert w._workspace_root() == docs.resolve()


def test_config_user_home_project_path_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "remedy-runtime.exe").write_text("x", encoding="utf-8")
    (install / "webui").mkdir()
    (install / "windows").mkdir()

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    rem_home = fake_home / ".remedy"
    rem_home.mkdir()
    # Config points at the entire profile — must not become the workspace.
    (rem_home / "config.toml").write_text(
        f'project_path = "{fake_home.as_posix()}"\n', encoding="utf-8"
    )

    monkeypatch.delenv("REMEDY_WORKSPACE", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT_PATH", raising=False)
    monkeypatch.delenv("REMEDY_PROJECT", raising=False)
    monkeypatch.delenv("REMEDY_FILES_ROOT", raising=False)
    monkeypatch.setenv("REMEDY_HOME", str(rem_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(install)

    got = w._workspace_root()
    assert got == (fake_home / "Documents" / "Remedy").resolve()

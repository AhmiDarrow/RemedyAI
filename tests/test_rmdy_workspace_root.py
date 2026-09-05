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

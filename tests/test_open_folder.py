"""OS folder open — explorer/start argv must not wait on explorer.exe."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.open_folder import (
    existing_dir,
    folder_from_argv,
    folder_from_command,
    format_open_folder_result,
    open_folder_os,
)


def test_folder_from_argv_explorer(tmp_path: Path):
    d = tmp_path / "example-folder"
    d.mkdir()
    assert folder_from_argv(["explorer", str(d)]) == d.resolve()
    assert folder_from_argv(["explorer.exe", str(d)]) == d.resolve()
    assert folder_from_argv([r"C:\WINDOWS\explorer.EXE", str(d)]) == d.resolve()
    assert folder_from_argv(["cmd", "/c", "start", "", str(d)]) == d.resolve()
    assert folder_from_argv(["python", "-c", "print(1)"]) is None
    assert folder_from_argv(["explorer"]) is None


def test_folder_from_command_start(tmp_path: Path):
    d = tmp_path / "bot"
    d.mkdir()
    assert folder_from_command(f'start "" "{d}"') == d.resolve()
    assert folder_from_command(f'explorer "{d}"') == d.resolve()
    assert folder_from_command("echo hi") is None


def test_existing_dir_rejects_file(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("x", encoding="utf-8")
    assert existing_dir(str(f)) is None
    assert existing_dir(str(tmp_path)) == tmp_path.resolve()


def test_open_folder_os_mocked(tmp_path: Path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(
        "os.startfile", lambda p: opened.append(str(p)), raising=False
    )
    monkeypatch.setattr("os.name", "nt")
    info = open_folder_os(tmp_path)
    assert info["ok"] is True
    assert Path(info["target"]) == tmp_path.resolve()
    if opened:
        assert Path(opened[0]).resolve() == tmp_path.resolve()
    text = format_open_folder_result(info)
    assert "opened folder" in text
    assert "open_panel" in text


def test_open_folder_os_refuses_missing(tmp_path: Path):
    with pytest.raises(ValueError, match="not a directory"):
        open_folder_os(tmp_path / "nope")


@pytest.mark.asyncio
async def test_host_run_explorer_does_not_wait_on_explorer(tmp_path: Path, monkeypatch):
    from tests.test_jail_scope_contract import _layout, _register

    home, proj, _ = _layout(tmp_path)
    d = tmp_path / "example-folder"
    d.mkdir()
    opened: list[str] = []

    def _fake_open(p):
        opened.append(str(p))
        return {"ok": True, "method": "startfile", "target": str(p)}

    monkeypatch.setattr("remedy.core.open_folder.open_folder_os", _fake_open)
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="full", home=home)
    out = await reg.execute("host_run", argv=["explorer", str(d)])
    assert "HOST_TRANSLATED_FAIL" not in out, out
    assert "opened folder" in out, out
    assert opened
    assert "open_panel" in out

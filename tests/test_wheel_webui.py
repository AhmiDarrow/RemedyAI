"""pip installs ship the browser UI: staging, wheel check, and serve handoff."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import remedy.webui as webui

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


stage_mod = _load_script("stage_wheel_webui")
check_mod = _load_script("check_wheel")


def _fake_build(root: Path) -> Path:
    src = root / "desktop-dist"
    (src / "assets").mkdir(parents=True)
    (src / "index.html").write_text("<!doctype html><title>Remedy Desktop</title>", encoding="utf-8")
    (src / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (src / "assets" / "index-abc.js.map").write_text("{}", encoding="utf-8")
    (src / "favicon.png").write_bytes(b"png")
    return src


def test_bundled_webui_dir_absent_in_checkout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(webui, "WEBUI_DIST_DIR", tmp_path / "dist")
    assert webui.bundled_webui_dir() is None
    assert webui.bundled_webui_version() is None


def test_stage_copies_build_without_source_maps(tmp_path) -> None:
    src = _fake_build(tmp_path)
    target = tmp_path / "staged"
    manifest = stage_mod.stage(src, target, "9.9.9")
    assert (target / "index.html").is_file()
    assert (target / "assets" / "index-abc.js").is_file()
    assert not (target / "assets" / "index-abc.js.map").exists()
    data = json.loads((target / stage_mod.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data == manifest
    assert data["version"] == "9.9.9"
    assert data["files"]["index.html"] == hashlib.sha256(
        (src / "index.html").read_bytes()
    ).hexdigest()


def test_bundled_webui_dir_found_after_stage(monkeypatch, tmp_path) -> None:
    target = tmp_path / "dist"
    stage_mod.stage(_fake_build(tmp_path), target, "9.9.9")
    monkeypatch.setattr(webui, "WEBUI_DIST_DIR", target)
    assert webui.bundled_webui_dir() == target
    assert webui.bundled_webui_version() == "9.9.9"


def test_stage_refuses_missing_build(tmp_path) -> None:
    with pytest.raises(SystemExit, match="index.html missing"):
        stage_mod.stage(tmp_path / "nope", tmp_path / "staged", "9.9.9")
    src = tmp_path / "half"
    src.mkdir()
    (src / "index.html").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="no JavaScript bundle"):
        stage_mod.stage(src, tmp_path / "staged", "9.9.9")


def _wheel(tmp_path: Path, version: str, *, ui_version: str | None, pins: dict | None = None,
           tamper: bool = False) -> Path:
    path = tmp_path / f"remedy_ai-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("remedy/__init__.py", "")
        if ui_version is not None:
            staged = tmp_path / f"staged-{ui_version}"
            stage_mod.stage(_fake_build(tmp_path / f"b-{ui_version}-{tamper}"), staged, ui_version)
            for f in staged.rglob("*"):
                if f.is_file():
                    data = f.read_bytes()
                    if tamper and f.name == "index.html":
                        data += b"<script>evil()</script>"
                    zf.writestr("remedy/webui/dist/" + f.relative_to(staged).as_posix(), data)
        zf.writestr("remedy/runtime/native_pins.json", json.dumps(
            pins if pins is not None else {"version": "", "sha256": {}}))
    return path


def test_check_wheel_accepts_wheel_with_ui(tmp_path) -> None:
    assert check_mod.check(_wheel(tmp_path, "1.2.3", ui_version="1.2.3"), release=False) == []


def test_check_wheel_refuses_wheel_without_ui(tmp_path) -> None:
    errors = check_mod.check(_wheel(tmp_path, "1.2.3", ui_version=None), release=False)
    assert any("index.html" in e for e in errors)
    assert check_mod.main([str(tmp_path / "remedy_ai-1.2.3-py3-none-any.whl")]) == 1


def test_check_wheel_refuses_stale_or_tampered_ui(tmp_path) -> None:
    stale = check_mod.check(_wheel(tmp_path, "1.2.3", ui_version="1.2.2"), release=False)
    assert any("staged for '1.2.2'" in e for e in stale)
    other = tmp_path / "t"
    other.mkdir()
    tampered = check_mod.check(_wheel(other, "1.2.3", ui_version="1.2.3", tamper=True), release=False)
    assert any("does not match the manifest" in e for e in tampered)


def test_check_wheel_release_requires_pins_for_its_version(tmp_path) -> None:
    unpinned = _wheel(tmp_path, "1.2.3", ui_version="1.2.3")
    assert any("no runtime pins" in e for e in check_mod.check(unpinned, release=True))
    other = tmp_path / "p"
    other.mkdir()
    wrong = _wheel(other, "1.2.3", ui_version="1.2.3",
                   pins={"version": "1.2.2", "sha256": {"remedy-runtime-x": "a" * 64}})
    assert any("pins are for '1.2.2'" in e for e in check_mod.check(wrong, release=True))
    good_dir = tmp_path / "g"
    good_dir.mkdir()
    good = _wheel(good_dir, "1.2.3", ui_version="1.2.3",
                  pins={"version": "1.2.3", "sha256": {"remedy-runtime-x": "a" * 64}})
    assert check_mod.check(good, release=True) == []


def _serve_env(monkeypatch, tmp_path) -> dict[str, str]:
    import remedy.interfaces.cli.cmd_runtime as CR

    called: dict[str, object] = {}

    def fake_call(cmd, *args, **kwargs):
        called["env"] = dict(kwargs.get("env") or {})
        return 0

    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(tmp_path / "remedy-runtime"))
    monkeypatch.setattr(CR.subprocess, "call", fake_call)
    monkeypatch.setattr(CR, "_print_bootstrap_link_when_ready", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        CR._cmd_serve(SimpleNamespace(
            home=str(tmp_path / "home"), host="127.0.0.1", port=7400, skip_setup=True,
            force_setup=False, config_file=None, computer_host=False, no_computer_host=False,
        ))
    env = called["env"]
    assert isinstance(env, dict)
    return env


def test_serve_hands_bundled_ui_to_runtime(monkeypatch, tmp_path) -> None:
    target = tmp_path / "dist"
    stage_mod.stage(_fake_build(tmp_path), target, "9.9.9")
    monkeypatch.setattr(webui, "WEBUI_DIST_DIR", target)
    monkeypatch.delenv("REMEDY_BUNDLED_WEBUI_DIR", raising=False)
    env = _serve_env(monkeypatch, tmp_path)
    assert env["REMEDY_BUNDLED_WEBUI_DIR"] == str(target)
    # Lowest priority only: an explicit REMEDY_WEBUI_DIR is left alone.
    assert "REMEDY_WEBUI_DIR" not in env or env["REMEDY_WEBUI_DIR"] != str(target)


def test_serve_without_bundled_ui_sets_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(webui, "WEBUI_DIST_DIR", tmp_path / "missing")
    monkeypatch.delenv("REMEDY_BUNDLED_WEBUI_DIR", raising=False)
    assert "REMEDY_BUNDLED_WEBUI_DIR" not in _serve_env(monkeypatch, tmp_path)

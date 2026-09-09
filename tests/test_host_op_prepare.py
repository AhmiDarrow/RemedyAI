"""Phase 3: Zig ``host_op_prepare`` matches Host Command IR prepare fixtures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as hb
from remedy.core.computer.host_binding import HostOp, prepare_host_command, prepare_host_op
from remedy.runtime import native_runtime

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "host_ir" / "prepare_argv_scriptfile.json"
)

_PLACEHOLDER_STEMS = {
    "<PWSH>": {"pwsh", "powershell"},
    "<CMD>": {"cmd"},
    "<GIT>": {"git"},
    "<PYTHON>": {"python", "python3", "pythonw"},
}


def _stem(token: str) -> str:
    name = Path(token).name
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _expected_host(value: str) -> str:
    """Resolve fixture host tokens. ``<DEFAULT>`` is cmd on Windows, posix elsewhere."""
    if value == "<DEFAULT>":
        return "cmd" if sys.platform == "win32" else "posix"
    return value


def _norm_argv_token(token: str, script_path: str | None) -> str:
    if script_path and os.path.normcase(token) == os.path.normcase(script_path):
        return "<SCRIPT_PATH>"
    stem = _stem(token)
    for placeholder, stems in _PLACEHOLDER_STEMS.items():
        if stem in stems:
            return placeholder
    return token


def _load_cases(kind: str) -> list[dict]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [c for c in raw["cases"] if c.get("kind") == kind]


def _assert_prepare_result(got: dict, expected: dict) -> None:
    assert got["kind"] == expected["kind"]

    if "argv" in expected:
        assert got.get("argv") == expected["argv"]
    if "argv_template" in expected:
        script_path = got.get("script_path")
        assert isinstance(script_path, str) or script_path is None
        normalized = [_norm_argv_token(a, script_path) for a in got.get("argv") or []]
        assert normalized == expected["argv_template"]
    if "display" in expected:
        assert got.get("display") == expected["display"]
    if "translated" in expected:
        assert got.get("translated") == expected["translated"]
    if "notes" in expected:
        assert got.get("notes") == expected["notes"]
    if "host" in expected:
        assert got.get("host") == _expected_host(str(expected["host"]))
    if "script_suffix" in expected:
        script_path = got["script_path"]
        assert isinstance(script_path, str)
        assert script_path.endswith(expected["script_suffix"])
        body = Path(script_path).read_bytes()
        if expected.get("script_has_bom"):
            assert body.startswith(b"\xef\xbb\xbf")
            body = body[3:]
        else:
            assert not body.startswith(b"\xef\xbb\xbf")
        if "script_body_utf8_sig" in expected:
            assert body.decode("utf-8") == expected["script_body_utf8_sig"]
    if "ir" in expected:
        ir = got.get("ir") or {}
        for key, value in expected["ir"].items():
            if key == "argv_template":
                script_path = None
                normalized = [_norm_argv_token(a, script_path) for a in ir.get("argv") or []]
                assert normalized == value
                continue
            assert ir.get(key) == value


@pytest.fixture(scope="module")
def _require_abi4_core():
    if native_runtime._core_library_path() is None:
        pytest.skip("remedy_core is not built in this checkout")
    library = native_runtime.core_library()
    assert int(library.remedy_core_abi_version()) == native_runtime._ABI_VERSION


@pytest.mark.usefixtures("_require_abi4_core")
@pytest.mark.parametrize("case", _load_cases("prepare_op"), ids=lambda c: c["id"])
def test_host_op_prepare_matches_fixture(case: dict, tmp_path: Path) -> None:
    op = case["input"]["op"]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    got = hb.host_op_prepare(op=op, scratch_dir=str(scratch))
    _assert_prepare_result(got, case["expected"])


@pytest.mark.usefixtures("_require_abi4_core")
@pytest.mark.parametrize("case", _load_cases("prepare_command"), ids=lambda c: c["id"])
def test_host_prepare_command_matches_fixture(case: dict, tmp_path: Path) -> None:
    inp = case["input"]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    payload: dict = {"command": inp["command"], "scratch_dir": str(scratch)}
    if "host" in inp:
        payload["host"] = inp["host"]
    got = hb.host_op_prepare(raw=payload)
    _assert_prepare_result(got, case["expected"])


@pytest.mark.usefixtures("_require_abi4_core")
def test_prepare_host_op_routes_structured_ops_through_zig(tmp_path: Path) -> None:
    """Production prepare_host_op must not keep a Python twin for structured ops."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    op = HostOp(kind="run", argv=["git", "status"])
    prepared = prepare_host_op(op, scratch_dir=scratch)
    assert prepared.kind == "argv"
    assert len(prepared.argv) >= 2
    assert _stem(prepared.argv[0]) == "git"
    assert prepared.argv[1:] == ["status"]
    assert prepared.ir.kind == "run"

    script = prepare_host_op(
        HostOp(kind="script", lang="pwsh", body="Write-Output 'hi'"),
        scratch_dir=scratch,
    )
    assert script.kind == "script"
    assert script.script_path is not None
    assert script.script_path.suffix.lower() == ".ps1"
    assert script.host == "pwsh"
    assert script.argv[1:5] == [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
    ]


@pytest.mark.usefixtures("_require_abi4_core")
def test_prepare_host_command_routes_through_zig(tmp_path: Path) -> None:
    """Production prepare_host_command is Zig-only (no Python rewrite twin)."""
    if sys.platform != "win32":
        pytest.skip("cmd host translation fixtures are Windows-oriented")
    prepared = prepare_host_command("chmod +x run.sh", host="cmd")
    assert prepared.kind == "noop"
    assert prepared.argv == []

    mkdir = prepare_host_command("mkdir -p src/x", host="cmd", scratch_dir=tmp_path)
    assert mkdir.kind == "translated"
    assert "if not exist" in mkdir.display

    ps = prepare_host_command("Get-ChildItem -Name", scratch_dir=tmp_path)
    assert ps.kind == "script"
    assert ps.script_path is not None
    assert "-File" in ps.argv
    assert "-Command" not in ps.argv


@pytest.mark.usefixtures("_require_abi4_core")
def test_prepare_host_op_raw_uses_zig_command_path() -> None:
    if sys.platform != "win32":
        pytest.skip("cmd host translation fixtures are Windows-oriented")
    prepared = prepare_host_op(HostOp(kind="raw", text="chmod +x run.sh", host="cmd"))
    assert prepared.kind == "noop"
    assert prepared.argv == []


@pytest.mark.usefixtures("_require_abi4_core")
def test_prepare_host_op_default_host_follows_os(tmp_path: Path) -> None:
    prepared = prepare_host_op(
        HostOp(kind="run", argv=["git", "status"]),
        scratch_dir=tmp_path,
    )
    assert prepared.host == ("cmd" if sys.platform == "win32" else "posix")

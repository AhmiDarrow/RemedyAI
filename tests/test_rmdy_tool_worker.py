"""Targeted tests for the RMDY Python tool worker product surface."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from remedy.runtime import rmdy_tool_worker as worker


def _frame(kind: int, payload: bytes, correlation: bytes | None = None) -> bytes:
    corr = correlation or (b"\x01" + b"\x00" * 15)
    header = b"RMDY" + struct.pack("<HHII", 1, kind, 0, len(payload)) + corr
    return header + payload


def test_workspace_read_and_list_handlers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "note.txt"
    sample.write_text("hello partner\nline two\n", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    read = worker._workspace_read({"path": "note.txt"})
    assert read["path"] == "note.txt"
    assert "hello partner" in read["content"]

    listed = worker._workspace_list({"path": ".", "limit": 10})
    names = {e["name"] for e in listed["entries"]}
    assert "note.txt" in names
    assert any(e["kind"] == "dir" for e in listed["entries"])


def test_workspace_read_refuses_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    with pytest.raises(PermissionError):
        worker._workspace_read({"path": "../outside.txt"})


def test_stdio_tool_round_trip_workspace_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    req = json.dumps(
        {"tool_id": "workspace.list", "version": 1, "input": {"path": ".", "limit": 5}}
    ).encode("utf-8")
    corr = b"\x02" + b"\x00" * 15
    inbound = _frame(worker._KIND_TOOL_REQUEST, req, corr)

    class _Buf:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0
            self.out = bytearray()

        def read(self, n: int) -> bytes:
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def write(self, data: bytes) -> int:
            self.out.extend(data)
            return len(data)

        def flush(self) -> None:
            return None

    buf = _Buf(inbound)
    # serve exits on EOF after one request
    worker.serve(buf, buf)  # type: ignore[arg-type]

    raw = bytes(buf.out)
    assert raw.startswith(b"RMDY")
    payload_len = struct.unpack_from("<I", raw, 12)[0]
    payload = raw[32 : 32 + payload_len]
    body = json.loads(payload.decode("utf-8"))
    assert body["ok"] is True
    assert body["output"]["total"] >= 1
    assert any(e["name"] == "a.py" for e in body["output"]["entries"])


def test_workspace_write_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    out = worker._workspace_write({"path": "nested/hi.txt", "content": "hello abi\n"})
    assert out["path"] == "nested/hi.txt"
    assert out["created"] is True
    assert out["bytes_written"] == len(b"hello abi\n")
    assert (tmp_path / "nested" / "hi.txt").read_text(encoding="utf-8") == "hello abi\n"

    again = worker._workspace_write({"path": "nested/hi.txt", "content": "updated\n"})
    assert again["created"] is False
    assert (tmp_path / "nested" / "hi.txt").read_text(encoding="utf-8") == "updated\n"


def test_workspace_write_refuses_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    with pytest.raises(PermissionError):
        worker._workspace_write({"path": "../outside.txt", "content": "nope"})


def test_workspace_write_refuses_history_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="history"):
        worker._workspace_write(
            {
                "path": "a.py",
                "content": "[file_write content omitted from provider history]",
            }
        )


def test_workspace_edit_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "edit_me.py"
    target.write_text("def hello():\n    return 1\n", encoding="utf-8")
    out = worker._workspace_edit(
        {
            "path": "edit_me.py",
            "old_string": "return 1",
            "new_string": "return 2",
        }
    )
    assert out["changed"] is True
    assert out["occurrences"] == 1
    assert out["hunks_applied"] == 1
    assert target.read_text(encoding="utf-8") == "def hello():\n    return 2\n"


def test_workspace_edit_refuses_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    outside = tmp_path.parent / "outside_edit.txt"
    outside.write_text("keep", encoding="utf-8")
    with pytest.raises(PermissionError):
        worker._workspace_edit(
            {
                "path": "../outside_edit.txt",
                "old_string": "keep",
                "new_string": "gone",
            }
        )
    assert outside.read_text(encoding="utf-8") == "keep"


def test_workspace_edit_registered() -> None:
    assert ("workspace.edit", 1) in worker._HANDLERS


def test_stdio_tool_round_trip_workspace_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))

    req = json.dumps(
        {
            "tool_id": "workspace.write",
            "version": 1,
            "input": {"path": "w.txt", "content": "wire"},
        }
    ).encode("utf-8")
    corr = b"\x04" + b"\x00" * 15
    inbound = _frame(worker._KIND_TOOL_REQUEST, req, corr)

    class _Buf:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0
            self.out = bytearray()

        def read(self, n: int) -> bytes:
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def write(self, data: bytes) -> int:
            self.out.extend(data)
            return len(data)

        def flush(self) -> None:
            return None

    buf = _Buf(inbound)
    worker.serve(buf, buf)  # type: ignore[arg-type]

    raw = bytes(buf.out)
    payload_len = struct.unpack_from("<I", raw, 12)[0]
    body = json.loads(raw[32 : 32 + payload_len].decode("utf-8"))
    assert body["ok"] is True
    assert body["output"]["path"] == "w.txt"
    assert (tmp_path / "w.txt").read_text(encoding="utf-8") == "wire"


def test_workspace_search_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "alpha.py").write_text("def partner_token():\n    return 1\n", encoding="utf-8")
    (tmp_path / "skip_me").mkdir()
    (tmp_path / "skip_me" / "other.py").write_text("partner_token = 0\n", encoding="utf-8")

    out = worker._workspace_search(
        {"pattern": "partner_token", "path": ".", "max_matches": 10, "case_insensitive": False}
    )
    assert out["pattern"] == "partner_token"
    assert out["total"] >= 1
    assert any("partner_token" in m["text"] for m in out["matches"])
    assert all(isinstance(m["line"], int) and m["line"] >= 1 for m in out["matches"])


def test_workspace_search_skips_credential_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("SECRET_MARKER = 'code'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_MARKER=sk-live-1\n", encoding="utf-8")
    (tmp_path / "server.pem").write_text("SECRET_MARKER pem\n", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("SECRET_MARKER key\n", encoding="utf-8")
    auth = tmp_path / ".remedy" / "auth"
    auth.mkdir(parents=True)
    (auth / "tokens.txt").write_text("SECRET_MARKER token\n", encoding="utf-8")
    from remedy.core import rg_binary

    for no_rg in (False, True):
        if no_rg:
            monkeypatch.setattr(rg_binary, "find_rg", lambda *a, **k: (None, "none"))
            monkeypatch.setattr(rg_binary, "schedule_ensure_rg", lambda *a, **k: None)
        out = worker._workspace_search({"pattern": "SECRET_MARKER", "path": ".", "max_matches": 50})
        paths = {m["path"] for m in out["matches"]}
        assert paths == {"app.py"}, (out["engine"], paths)
        from remedy.core.errors import SecurityError

        for secret in (".env", "server.pem", ".remedy/auth/tokens.txt"):
            with pytest.raises((PermissionError, SecurityError)):
                worker._workspace_search({"pattern": "SECRET_MARKER", "path": secret})


def test_workspace_search_refuses_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    with pytest.raises(PermissionError):
        worker._workspace_search({"pattern": "x", "path": "../outside"})


def test_workspace_search_requires_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="pattern"):
        worker._workspace_search({"pattern": "  "})


def test_stdio_tool_round_trip_workspace_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "hit.txt").write_text("find-me-abi\n", encoding="utf-8")

    req = json.dumps(
        {
            "tool_id": "workspace.search",
            "version": 1,
            "input": {"pattern": "find-me-abi", "max_matches": 5},
        }
    ).encode("utf-8")
    corr = b"\x06" + b"\x00" * 15
    inbound = _frame(worker._KIND_TOOL_REQUEST, req, corr)

    class _Buf:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0
            self.out = bytearray()

        def read(self, n: int) -> bytes:
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def write(self, data: bytes) -> int:
            self.out.extend(data)
            return len(data)

        def flush(self) -> None:
            return None

    buf = _Buf(inbound)
    worker.serve(buf, buf)  # type: ignore[arg-type]

    raw = bytes(buf.out)
    payload_len = struct.unpack_from("<I", raw, 12)[0]
    body = json.loads(raw[32 : 32 + payload_len].decode("utf-8"))
    assert body["ok"] is True
    assert body["output"]["total"] >= 1
    assert any("find-me-abi" in m["text"] for m in body["output"]["matches"])






























def test_voice_vision_handlers_registered() -> None:
    for key in (
        ("voice.speak", 1),
        ("voice.transcribe", 1),
        ("voice.install", 1),
        ("vision.activate", 1),
        ("vision.install", 1),
        ("vision.cancel_install", 1),
        ("vision.reinstall_runtime", 1),
        ("vision.uninstall", 1),
        ("vision.start", 1),
        ("vision.stop", 1),
        ("vision.progress", 1),
    ):
        assert key in worker._HANDLERS


def test_voice_install_unknown_component() -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    out = vv.voice_install({"component": "nope"})
    assert out["ok"] is False
    assert out["started"] is False
    assert "Unknown" in str(out.get("error") or "")


def test_vision_progress_idle() -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    out = vv.vision_progress({})
    assert isinstance(out, dict)
    assert out.get("phase") == "idle"


def test_voice_speak_unavailable_without_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))

    def _no_synth(*_a, **_k):
        return None

    monkeypatch.setattr("remedy.voice.service.synthesize", _no_synth)
    out = vv.voice_speak({"text": "hi", "home_dir": str(tmp_path)})
    assert out["unavailable"] is True
    assert out["wav_b64"] == ""























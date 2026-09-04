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


def test_stdio_tool_round_trip_workspace_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

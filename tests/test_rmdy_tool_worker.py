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


def test_web_search_bridges_agent_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)

    def _fake_run_search(q, *, max_results, timeout, runtime=None):
        assert q == "asyncio gather"
        assert max_results == 2
        return (
            [
                {
                    "title": "asyncio docs",
                    "url": "https://docs.python.org/3/library/asyncio.html",
                    "snippet": "gather coroutines",
                }
            ],
            "test-backend",
        )

    monkeypatch.setattr(web, "run_search", _fake_run_search)
    out = worker._web_search({"query": "asyncio gather", "max_results": 2})
    assert out["query"] == "asyncio gather"
    assert out["backend"] == "test-backend"
    assert out["results"][0]["url"].startswith("https://docs.python.org/")


def test_web_search_requires_query(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)
    with pytest.raises(ValueError, match="query"):
        worker._web_search({"query": "  "})


def test_stdio_tool_round_trip_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)
    monkeypatch.setattr(
        web,
        "run_search",
        lambda q, *, max_results, timeout, runtime=None: (
            [{"title": "t", "url": "https://example.com/", "snippet": "s"}],
            "stub",
        ),
    )

    req = json.dumps(
        {"tool_id": "web.search", "version": 1, "input": {"query": "example", "max_results": 1}}
    ).encode("utf-8")
    corr = b"\x03" + b"\x00" * 15
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
    assert body["output"]["backend"] == "stub"
    assert body["output"]["results"][0]["title"] == "t"


def test_web_fetch_bridges_polite_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)
    html = (
        b"<!doctype html><html><head><title>Guide</title></head>"
        b"<body><article><h1>Guide</h1><p>Hello partner.</p></article></body></html>"
    )

    def _fake_polite_fetch(url, *, max_chars, timeout, runtime=None, respect_robots=None):
        assert url == "https://example.com/guide"
        return url, html, "utf-8"

    monkeypatch.setattr(web, "polite_fetch", _fake_polite_fetch)
    out = worker._web_fetch({"url": "https://example.com/guide", "max_chars": 5000})
    assert out["url"] == "https://example.com/guide"
    assert out["final_url"] == "https://example.com/guide"
    assert out["format"] == "markdown"
    assert "Hello partner" in out["content"]
    assert out.get("title") == "Guide"


def test_web_fetch_requires_http_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)
    with pytest.raises(ValueError, match="http"):
        worker._web_fetch({"url": "file:///etc/passwd"})


def test_web_fetch_refuses_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: False)
    with pytest.raises(PermissionError, match="disabled"):
        worker._web_fetch({"url": "https://example.com/"})


def test_stdio_tool_round_trip_web_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import remedy.core.web_helpers as web

    monkeypatch.setattr(web, "web_tools_enabled", lambda runtime=None: True)
    monkeypatch.setattr(
        web,
        "polite_fetch",
        lambda url, *, max_chars, timeout, runtime=None, respect_robots=None: (
            url,
            b"plain text body",
            "utf-8",
        ),
    )

    req = json.dumps(
        {
            "tool_id": "web.fetch",
            "version": 1,
            "input": {"url": "https://example.com/plain", "max_chars": 2000},
        }
    ).encode("utf-8")
    corr = b"\x05" + b"\x00" * 15
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
    assert body["output"]["format"] == "text"
    assert body["output"]["content"] == "plain text body"
    assert body["output"]["url"] == "https://example.com/plain"


def test_prompt_assemble_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    out = worker._prompt_assemble(
        {"message": "hello partner", "session_id": "sess-1", "chat_mode": True}
    )
    assert isinstance(out["system"], str) and out["system"].strip()
    assert out["goal"] == "hello partner"
    assert int(out["system_chars"]) == len(out["system"])
    # Identity / operational body must beat raw prompt-only.
    assert "Remedy" in out["system"] or "partner" in out["system"].lower()


def test_prompt_assemble_does_not_register_agent_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 6 absolute: prompt path must not boot the agent_* tool forest."""
    from remedy.runtime import prompt_assemble as pa

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    pa._runtime_cache.clear()
    out = worker._prompt_assemble(
        {"message": "tool-free harness", "session_id": "sess-tools", "chat_mode": True}
    )
    assert isinstance(out["system"], str) and out["system"].strip()
    cached = next(iter(pa._runtime_cache.values()), None)
    assert cached is not None
    assert getattr(cached, "_register_tools", True) is False
    names = {t.name for t in cached.tool_registry.tools}
    handlers = set(getattr(cached.tool_registry, "_handlers", {}) or {})
    assert not names
    assert "file_read" not in handlers
    assert "bash_exec" not in handlers
    assert "memory_search" not in handlers


def test_stdio_tool_round_trip_prompt_assemble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    req = json.dumps(
        {
            "tool_id": "prompt.assemble",
            "version": 1,
            "input": {"message": "wire assemble", "session_id": "s"},
        }
    ).encode("utf-8")
    corr = b"\x07" + b"\x00" * 15
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
    assert body["output"]["goal"] == "wire assemble"
    assert isinstance(body["output"]["system"], str) and body["output"]["system"].strip()


def test_prompt_assemble_registered() -> None:
    assert ("prompt.assemble", 1) in worker._HANDLERS


def test_worker_web_handlers_import_web_helpers() -> None:
    """Phase 6 absolute: worker must not pull agent_web_tools registration."""
    import inspect

    src = inspect.getsource(worker._web_search) + inspect.getsource(worker._web_fetch)
    assert "web_helpers" in src
    assert "agent_web_tools" not in src


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


def test_memory_search_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )

    async def _fake_search(memory, query, *, limit=12, project_path=None):
        assert query == "favorite color"
        assert limit == 5
        return [
            {
                "kind": "fact",
                "title": "prefs",
                "content": "favorite color is teal",
                "score": 1.5,
                "authority": "owner",
                "inferred": False,
            }
        ]

    monkeypatch.setattr(
        "remedy.memory.partner_memory.search_partner_and_entries",
        _fake_search,
    )
    out = worker._memory_search({"query": "favorite color", "limit": 5})
    assert out["query"] == "favorite color"
    assert out["total"] == 1
    assert out["hits"][0]["content"] == "favorite color is teal"
    assert "context" in out["notice"].lower()


def test_memory_search_requires_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="query"):
        worker._memory_search({"query": "  "})


def test_stdio_tool_round_trip_memory_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )

    async def _fake_search(memory, query, *, limit=12, project_path=None):
        return [
            {
                "kind": "entry",
                "title": "note",
                "content": "wire memory hit",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(
        "remedy.memory.partner_memory.search_partner_and_entries",
        _fake_search,
    )

    req = json.dumps(
        {
            "tool_id": "memory.search",
            "version": 1,
            "input": {"query": "wire", "limit": 3},
        }
    ).encode("utf-8")
    corr = b"\x08" + b"\x00" * 15
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
    assert body["output"]["total"] == 1
    assert "wire memory hit" in body["output"]["hits"][0]["content"]


def test_memory_search_registered() -> None:
    assert ("memory.search", 1) in worker._HANDLERS


def test_memory_save_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    from remedy.runtime import prompt_assemble as pa

    pa._runtime_cache.clear()
    out = worker._memory_save(
        {
            "content": "Owner likes oat milk in coffee",
            "title": "Preference",
            "session_id": "sess-save",
        }
    )
    assert out["saved"] is True
    assert out["title"] == "Preference"
    assert out["parent_memory"] is True


def test_memory_save_refuses_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime import prompt_assemble as pa

    pa._runtime_cache.clear()
    with pytest.raises(PermissionError, match="secret"):
        worker._memory_save(
            {"content": "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"}
        )


def test_memory_save_registered() -> None:
    assert ("memory.save", 1) in worker._HANDLERS


def test_skill_search_and_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    from remedy.runtime import prompt_assemble as pa

    pa._runtime_cache.clear()
    ranked = worker._skill_search({"query": "change safety", "limit": 5})
    assert ranked["total"] >= 1
    names = {s["name"] for s in ranked["skills"]}
    assert "change-safety" in names or any("change" in n for n in names)
    pick = "change-safety" if "change-safety" in names else next(iter(names))
    activated = worker._skill_activate({"name": pick})
    assert activated["name"] == pick
    assert isinstance(activated["body"], str) and activated["body"].strip()
    assert int(activated["chars"]) == len(activated["body"])


def test_skill_activate_refuses_bulk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime import prompt_assemble as pa

    pa._runtime_cache.clear()
    with pytest.raises(PermissionError, match="bulk"):
        worker._skill_activate({"name": "all"})


def test_skill_handlers_registered() -> None:
    assert ("skill.search", 1) in worker._HANDLERS
    assert ("skill.activate", 1) in worker._HANDLERS



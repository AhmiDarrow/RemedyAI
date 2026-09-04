"""RMDY-framed Python tool worker for remedy-runtime.

Reads Remedy protocol frames (magic ``RMDY``) from a binary stream, answers
``KindHealth`` and ``KindToolRequest``, and writes ``KindToolResult`` responses.
Go owns the Tool ABI registry; this process only executes RuntimePython tools
over the versioned wire payloads defined in ``native/go/tools/frame_payload.go``.

Stdio mode (default)::

    python -m remedy.runtime.rmdy_tool_worker

The worker never logs to stdout (that is the wire).
"""

from __future__ import annotations

import json
import logging
import struct
import sys
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO

logger = logging.getLogger("remedy.runtime.rmdy_tool_worker")

_PROTOCOL_VERSION = 1
_HEADER_SIZE = 32
_MAX_PAYLOAD = 16 << 20
_MAGIC = b"RMDY"

_KIND_TOOL_REQUEST = 1
_KIND_TOOL_RESULT = 2
_KIND_HEALTH = 7

ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _slugify(text: str) -> str:
    lowered = text.lower()
    parts: list[str] = []
    last_hyphen = True
    for ch in lowered:
        if ch.isalnum():
            parts.append(ch)
            last_hyphen = False
        elif not last_hyphen:
            parts.append("-")
            last_hyphen = True
    return "".join(parts).strip("-")


def _word_count(text: str) -> int:
    return len(text.split())


_HANDLERS: dict[tuple[str, int], ToolHandler] = {
    ("text.slugify", 1): lambda inp: {"slug": _slugify(str(inp.get("text", "")))},
    ("text.word_count", 1): lambda inp: {"words": _word_count(str(inp.get("text", "")))},
}


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("rmdy stream closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> tuple[int, int, bytes, bytes]:
    header = _read_exact(stream, _HEADER_SIZE)
    if header[:4] != _MAGIC:
        raise ValueError("invalid RMDY magic")
    version, kind, flags, payload_len = struct.unpack_from("<HHII", header, 4)
    if version != _PROTOCOL_VERSION:
        raise ValueError(f"unsupported RMDY version {version}")
    if kind == 0:
        raise ValueError("invalid RMDY kind")
    if payload_len > _MAX_PAYLOAD:
        raise ValueError("RMDY payload too large")
    correlation = header[16:32]
    payload = _read_exact(stream, payload_len) if payload_len else b""
    return kind, flags, correlation, payload


def write_frame(stream: BinaryIO, kind: int, correlation: bytes, payload: bytes, *, flags: int = 0) -> None:
    if len(correlation) != 16:
        raise ValueError("correlation id must be 16 bytes")
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("RMDY payload too large")
    header = _MAGIC + struct.pack("<HHII", _PROTOCOL_VERSION, kind, flags, len(payload)) + correlation
    stream.write(header + payload)
    stream.flush()


def _handle_tool(payload: bytes) -> bytes:
    try:
        request = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return json.dumps({"ok": False, "error": f"invalid tool request: {exc}"}).encode("utf-8")
    tool_id = str(request.get("tool_id") or "")
    version = int(request.get("version") or 0)
    raw_input = request.get("input", {})
    if isinstance(raw_input, str):
        try:
            raw_input = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return json.dumps({"ok": False, "error": f"invalid input: {exc}"}).encode("utf-8")
    if not isinstance(raw_input, Mapping):
        return json.dumps({"ok": False, "error": "input must be a JSON object"}).encode("utf-8")
    handler = _HANDLERS.get((tool_id, version))
    if handler is None:
        return json.dumps({"ok": False, "error": f"unknown tool {tool_id}@{version}"}).encode("utf-8")
    try:
        output = handler(raw_input)
    except Exception as exc:  # noqa: BLE001 — wire must carry the failure
        logger.exception("tool %s@%s failed", tool_id, version)
        return json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
    return json.dumps({"ok": True, "output": output}, separators=(",", ":")).encode("utf-8")


def serve(reader: BinaryIO, writer: BinaryIO) -> None:
    while True:
        try:
            kind, _flags, correlation, payload = read_frame(reader)
        except EOFError:
            return
        if kind == _KIND_HEALTH:
            body = json.dumps(
                {"protocol": _PROTOCOL_VERSION, "ready": True, "capabilities": ["tools"]},
                separators=(",", ":"),
            ).encode("utf-8")
            write_frame(writer, _KIND_HEALTH, correlation, body)
            continue
        if kind == _KIND_TOOL_REQUEST:
            write_frame(writer, _KIND_TOOL_RESULT, correlation, _handle_tool(payload))
            continue
        err = f"unsupported RMDY frame kind {kind}".encode()
        write_frame(writer, _KIND_TOOL_RESULT, correlation, err, flags=1)


def main(argv: list[str] | None = None) -> int:
    _ = argv
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    # Windows stdio is text by default; reopen as binary for RMDY frames.
    serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

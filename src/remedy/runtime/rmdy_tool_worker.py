"""RMDY-framed Python tool worker for remedy-runtime.

Reads Remedy protocol frames (magic ``RMDY``) from a binary stream, answers
``KindHealth`` and ``KindToolRequest``, and writes ``KindToolResult`` responses.
Go owns the Tool ABI registry; this process only executes RuntimePython tools
over the versioned wire payloads defined in ``native/go/tools/frame_payload.go``.

Stdio mode (default)::

    python -m remedy.runtime.rmdy_tool_worker

IPC mode (supervised by ``remedy-runtime --serve``)::

    REMEDY_RMDY_ENDPOINT=\\\\.\\pipe\\remedy-tools-… python -m remedy.runtime.rmdy_tool_worker
    REMEDY_RMDY_ENDPOINT=/tmp/remedy-tools-….sock python -m remedy.runtime.rmdy_tool_worker

The worker never logs to stdout (that is the wire).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, cast

logger = logging.getLogger("remedy.runtime.rmdy_tool_worker")

_PROTOCOL_VERSION = 1
_HEADER_SIZE = 32
_MAX_PAYLOAD = 16 << 20
_MAGIC = b"RMDY"
_READ_CHAR_CAP = 512_000
_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "zig-cache",
    "zig-out",
}

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


def _looks_like_install_dir(path: Path) -> bool:
    """Packaged Desktop install folder — never a project workspace."""
    try:
        p = path.expanduser().resolve()
    except OSError:
        return False
    markers = (
        "Remedy Desktop.exe",
        "remedy-runtime.exe",
        "remedy-runtime",
        "uninstall.exe",
    )
    if any((p / name).exists() for name in markers):
        return True
    return (p / "webui").is_dir() and (p / "windows").is_dir()


def _workspace_root(inp: Mapping[str, Any] | None = None) -> Path:
    """Resolve the active project root.

    Prefer per-call workspace_root/project_path, then env, then config.toml,
    then a narrow owner folder (Documents/Remedy). Never fall back to the
    packaged Desktop install cwd or the entire user profile.
    """
    if inp:
        for key in ("workspace_root", "project_path"):
            raw = str(inp.get(key) or "").strip()
            if raw and raw not in {".", "./"}:
                try:
                    cand = Path(raw).expanduser().resolve()
                except OSError:
                    cand = Path(raw).expanduser().absolute()
                if not _is_user_home_path(cand) and not _looks_like_install_dir(cand):
                    return cand

    for key in ("REMEDY_WORKSPACE", "REMEDY_PROJECT_PATH", "REMEDY_PROJECT", "REMEDY_FILES_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw and raw not in {".", "./"}:
            try:
                cand = Path(raw).expanduser().resolve()
            except OSError:
                cand = Path(raw).expanduser().absolute()
            if not _is_user_home_path(cand) and not _looks_like_install_dir(cand):
                return cand

    home = (os.environ.get("REMEDY_HOME") or "").strip()
    if home:
        cfg = Path(home).expanduser() / "config.toml"
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or not s.lower().startswith("project_path"):
                continue
            if "=" not in s:
                continue
            val = s.split("=", 1)[1].strip().strip("\"'")
            # Writers escape Windows paths as C:\\Users\\… in TOML strings.
            val = val.replace("\\\\", "\\")
            if val and val not in {".", "./"}:
                try:
                    cand = Path(val).expanduser().resolve()
                except OSError:
                    cand = Path(val).expanduser().absolute()
                if not _is_user_home_path(cand) and not _looks_like_install_dir(cand):
                    return cand

    try:
        cwd = Path.cwd().resolve()
    except OSError:
        cwd = Path.cwd().absolute()
    if not _looks_like_install_dir(cwd) and not _is_user_home_path(cwd):
        return cwd

    return _default_owner_workspace()


def _is_user_home_path(path: Path) -> bool:
    try:
        home = Path.home().expanduser().resolve()
        return path.expanduser().resolve() == home
    except OSError:
        try:
            return path.expanduser().absolute() == Path.home().expanduser().absolute()
        except OSError:
            return False


def _default_owner_workspace() -> Path:
    """Narrow default — never the entire user profile."""
    try:
        user_home = Path.home().expanduser().resolve()
    except OSError:
        user_home = Path.home().expanduser().absolute()
    docs = user_home / "Documents" / "Remedy"
    try:
        docs.mkdir(parents=True, exist_ok=True)
        return docs.resolve()
    except OSError:
        pass
    fallback = user_home / ".remedy" / "workspace"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()
    except OSError:
        # Never jail to the entire profile when both owner folders fail.
        rem_home = (os.environ.get("REMEDY_HOME") or "").strip()
        if rem_home:
            try:
                p = Path(rem_home).expanduser() / "workspace"
                p.mkdir(parents=True, exist_ok=True)
                return p.resolve()
            except OSError:
                pass
        return docs


def _resolve_workspace_path(path: str, inp: Mapping[str, Any] | None = None) -> Path:
    root = _workspace_root(inp)
    raw = (path or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"path escapes workspace root: {path}") from exc
    return resolved


def _is_credential_name(name: str) -> bool:
    try:
        from remedy.core.security import is_credential_filename

        return bool(is_credential_filename(name))
    except Exception:  # noqa: BLE001 — worker must stay up without full package
        lowered = name.lower()
        return lowered in {".env", ".npmrc", ".pypirc"} or lowered.endswith(
            (".pem", ".key", ".p12", ".pfx")
        )


def _is_junk_listing_name(name: str) -> bool:
    """Hide corrupt / private-use names (e.g. 'C' + U+F03A) from listings."""
    if not name or name in {".", ".."}:
        return True
    for ch in name:
        o = ord(ch)
        if o < 32 or o == 127 or 0xE000 <= o <= 0xF8FF:
            return True
    return False


def _workspace_read(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    path = str(inp.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")
    target = _resolve_workspace_path(path, inp)
    if _is_credential_name(target.name) or any(_is_credential_name(p) for p in target.parts):
        raise PermissionError("credential-looking files are not readable")
    if not target.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if target.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")
    try:
        from remedy.core.text_files import is_probably_text

        if not is_probably_text(target):
            raise ValueError(f"binary or non-text file: {path}")
    except ImportError:
        pass
    text = target.read_text(encoding="utf-8", errors="replace")
    try:
        offset = max(0, int(inp.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    limit_raw = inp.get("limit")
    limit: int | None
    try:
        limit = None if limit_raw is None else max(1, int(limit_raw))
    except (TypeError, ValueError):
        limit = None
    truncated = False
    if offset or limit is not None:
        lines = text.splitlines(keepends=True)
        end = len(lines) if limit is None else min(len(lines), offset + limit)
        start = min(offset, len(lines))
        text = "".join(lines[start:end])
        truncated = end < len(lines)
    if len(text) > _READ_CHAR_CAP:
        text = text[:_READ_CHAR_CAP]
        truncated = True
    try:
        rel = str(target.relative_to(_workspace_root(inp)).as_posix())
    except ValueError:
        rel = str(target)
    out: dict[str, Any] = {"path": rel, "content": text}
    if truncated:
        out["truncated"] = True
    return out


def _workspace_list(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    path = str(inp.get("path") or ".").strip() or "."
    target = _resolve_workspace_path(path, inp)
    if not target.exists():
        raise FileNotFoundError(f"path not found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    try:
        limit = max(1, min(2000, int(inp.get("limit") or 200)))
    except (TypeError, ValueError):
        limit = 200
    try:
        offset = max(0, int(inp.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    root = _workspace_root(inp)
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    visible = [
        p
        for p in entries
        if p.name not in _SKIP_DIR_NAMES
        and not _is_credential_name(p.name)
        and not _is_junk_listing_name(p.name)
    ]
    page = visible[offset : offset + limit]
    items: list[dict[str, str]] = []
    for p in page:
        try:
            name = p.relative_to(root).as_posix()
        except ValueError:
            name = p.name
        items.append({"name": name, "kind": "dir" if p.is_dir() else "file"})
    out: dict[str, Any] = {
        "path": path,
        "entries": items,
        "total": len(visible),
    }
    if offset + len(items) < len(visible):
        out["truncated"] = True
    return out


def _workspace_write(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI workspace.write to atomic workspace file write + agent guards."""
    path = str(inp.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")
    if "content" not in inp:
        raise ValueError("content is required")
    raw_content = inp.get("content")
    if raw_content is None:
        raise ValueError("content is required")
    if isinstance(raw_content, (dict, list)):
        body = json.dumps(raw_content, ensure_ascii=False)
    elif isinstance(raw_content, str):
        body = raw_content
    else:
        body = str(raw_content)

    from remedy.core.workspace_tools.guards import (
        junk_write_guard,
        looks_like_history_stub_text,
        reserved_guard,
    )

    bad = reserved_guard(path)
    if bad:
        raise PermissionError(bad)
    junk = junk_write_guard(path)
    if junk:
        raise PermissionError(junk)
    if looks_like_history_stub_text(body):
        raise ValueError("refusing to write provider-history summary stub as file content")

    target = _resolve_workspace_path(path, inp)
    if _is_credential_name(target.name) or any(_is_credential_name(p) for p in target.parts):
        raise PermissionError("credential-looking files are not writable")
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")

    created = not target.is_file()
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(target, body)
    try:
        rel = str(target.relative_to(_workspace_root(inp)).as_posix())
    except ValueError:
        rel = str(target)
    return {
        "path": rel,
        "bytes_written": len(body.encode("utf-8")),
        "created": created,
    }


def _workspace_edit(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI workspace.edit to jailed search/replace (file_edit parity)."""
    path = str(inp.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")

    from remedy.core.workspace_tools.guards import reserved_guard

    bad = reserved_guard(path)
    if bad:
        raise PermissionError(bad)

    target = _resolve_workspace_path(path, inp)
    if _is_credential_name(target.name) or any(
        _is_credential_name(p) for p in target.parts
    ):
        raise PermissionError("credential-looking files are not editable")
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {path}")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not UTF-8 text: {path}") from exc

    from remedy.core.file_edit import apply_multi_hunk, apply_search_replace

    hunks_raw = inp.get("edits")
    if hunks_raw is not None:
        if not isinstance(hunks_raw, list) or not hunks_raw:
            raise ValueError("edits must be a non-empty list of {old_string,new_string}")
        result = apply_multi_hunk(content, hunks_raw)
    else:
        old_string = inp.get("old_string")
        if old_string is None:
            raise ValueError("old_string is required (or pass edits=[...])")
        new_string = inp.get("new_string")
        if new_string is None:
            raise ValueError("new_string is required")
        replace_all = bool(inp.get("replace_all") or False)
        result = apply_search_replace(
            content,
            str(old_string),
            str(new_string),
            replace_all=replace_all,
        )

    if not result.ok or result.new_content is None:
        raise ValueError(result.message or "edit failed")

    if result.new_content != content:
        from remedy.core.atomic_json import write_text_atomic

        write_text_atomic(target, result.new_content)

    try:
        rel = str(target.relative_to(_workspace_root(inp)).as_posix())
    except ValueError:
        rel = str(target)
    return {
        "path": rel,
        "occurrences": int(result.occurrences or 0),
        "hunks_applied": int(result.hunks_applied or 0),
        "message": str(result.message or ""),
        "changed": result.new_content != content,
    }


def _workspace_search(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI workspace.search to repo_search (rg or Python sniff)."""
    pattern = str(inp.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    path = str(inp.get("path") or ".").strip() or "."
    glob = str(inp.get("glob") or "").strip() or None
    raw_max = inp.get("max_matches", 50)
    try:
        max_matches = int(raw_max) if raw_max is not None else 50
    except (TypeError, ValueError):
        max_matches = 50
    max_matches = max(1, min(500, max_matches))
    case_insensitive = bool(inp.get("case_insensitive") or False)

    root = _workspace_root(inp)
    # Keep absolute paths inside the workspace jail (fail closed).
    if path not in (".", "./", ""):
        _resolve_workspace_path(path, inp)

    from remedy.core.repo_search import search_repo

    home = (os.environ.get("REMEDY_HOME") or "").strip() or None
    hits, engine = search_repo(
        root,
        pattern,
        path=path,
        glob=glob,
        max_matches=max_matches,
        case_insensitive=case_insensitive,
        home_dir=home,
        allowed_roots=[root],
        access_scope="project",
    )
    if str(engine).startswith("error:"):
        raise PermissionError(str(engine)[len("error:") :].strip() or engine)
    matches: list[dict[str, Any]] = []
    for hit in hits:
        matches.append(
            {
                "path": str(hit.path),
                "line": int(hit.line),
                "text": str(hit.text),
            }
        )
    return {
        "pattern": pattern,
        "engine": str(engine),
        "matches": matches,
        "total": len(matches),
    }


def _web_search(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI web.search to the existing agent web_search backend."""
    from remedy.core.web_helpers import run_search, web_tools_enabled

    if not web_tools_enabled(None):
        raise PermissionError("web tools are disabled")
    query = str(inp.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > 400:
        query = query[:400]
    raw_max = inp.get("max_results", 5)
    try:
        max_results = int(raw_max) if raw_max is not None else 5
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(10, max_results))
    rows, backend = run_search(query, max_results=max_results, timeout=20.0, runtime=None)
    results: list[dict[str, str]] = []
    for row in rows:
        item: dict[str, str] = {
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
        }
        snippet = str(row.get("snippet") or "").strip()
        if snippet:
            item["snippet"] = snippet
        results.append(item)
    return {"query": query, "backend": str(backend), "results": results}


def _web_fetch(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI web.fetch to polite_fetch + HTML extract (SSRF-guarded)."""
    from remedy.core.web_helpers import polite_fetch, web_tools_enabled

    if not web_tools_enabled(None):
        raise PermissionError("web tools are disabled")
    url = str(inp.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    raw_cap = inp.get("max_chars", 50_000)
    try:
        cap = int(raw_cap) if raw_cap is not None else 50_000
    except (TypeError, ValueError):
        cap = 50_000
    cap = max(1000, min(200_000, cap))

    final_url, raw, charset = polite_fetch(
        url, max_chars=max(cap * 4, 200_000), timeout=25.0, runtime=None
    )
    text = raw.decode(charset or "utf-8", errors="replace")
    from remedy.core.html_extract import html_to_markdown, looks_like_html

    out: dict[str, Any] = {
        "url": url,
        "final_url": str(final_url or url),
        "content": text,
        "format": "text",
    }
    truncated = False
    if looks_like_html(raw):
        extracted = html_to_markdown(text, max_chars=cap)
        md = str(extracted.get("markdown") or "")
        body = md.strip()
        title = str(extracted.get("title") or "").strip()
        if body:
            out["content"] = body
            out["format"] = "markdown"
            if title:
                out["title"] = title
            truncated = f"…[truncated at {cap} chars]" in md
        else:
            if len(text) > cap:
                out["content"] = text[:cap]
                truncated = True
            if title:
                out["title"] = title
    elif len(text) > cap:
        out["content"] = text[:cap]
        truncated = True
    if truncated:
        out["truncated"] = True
    return out


def _prompt_assemble(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.prompt_assemble import assemble_prompt

    return assemble_prompt(inp)


def _voice_speak(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_speak

    return voice_speak(inp)


def _voice_transcribe(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_transcribe

    return voice_transcribe(inp)


def _voice_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_install

    return voice_install(inp)


def _vision_activate(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_activate

    return vision_activate(inp)


def _vision_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_install

    return vision_install(inp)


def _vision_cancel_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_cancel_install

    return vision_cancel_install(inp)


def _vision_reinstall_runtime(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_reinstall_runtime

    return vision_reinstall_runtime(inp)


def _vision_uninstall(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_uninstall

    return vision_uninstall(inp)


def _vision_start(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_start

    return vision_start(inp)


def _vision_stop(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_stop

    return vision_stop(inp)


def _vision_progress(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_progress

    return vision_progress(inp)


def _memory_search(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI memory.search to Partner Memory + FTS (forever-Python)."""
    from remedy.runtime.prompt_assemble import search_memory

    return search_memory(inp)


def _memory_save(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI memory.save to Partner Memory write (forever-Python)."""
    from remedy.runtime.prompt_assemble import save_memory

    return save_memory(inp)


def _skill_search(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI skill.search to SkillRegistry ranking."""
    from remedy.runtime.prompt_assemble import search_skills

    return search_skills(inp)


def _skill_activate(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bridge Tool ABI skill.activate to one skill body load."""
    from remedy.runtime.prompt_assemble import activate_skill

    return activate_skill(inp)


_HANDLERS: dict[tuple[str, int], ToolHandler] = {
    ("text.slugify", 1): lambda inp: {"slug": _slugify(str(inp.get("text", "")))},
    ("text.word_count", 1): lambda inp: {"words": _word_count(str(inp.get("text", "")))},
    ("workspace.read", 1): _workspace_read,
    ("workspace.list", 1): _workspace_list,
    ("workspace.write", 1): _workspace_write,
    ("workspace.edit", 1): _workspace_edit,
    ("workspace.search", 1): _workspace_search,
    ("web.search", 1): _web_search,
    ("web.fetch", 1): _web_fetch,
    ("prompt.assemble", 1): _prompt_assemble,
    ("voice.speak", 1): _voice_speak,
    ("voice.transcribe", 1): _voice_transcribe,
    ("voice.install", 1): _voice_install,
    ("vision.activate", 1): _vision_activate,
    ("vision.install", 1): _vision_install,
    ("vision.cancel_install", 1): _vision_cancel_install,
    ("vision.reinstall_runtime", 1): _vision_reinstall_runtime,
    ("vision.uninstall", 1): _vision_uninstall,
    ("vision.start", 1): _vision_start,
    ("vision.stop", 1): _vision_stop,
    ("vision.progress", 1): _vision_progress,
    ("memory.search", 1): _memory_search,
    ("memory.save", 1): _memory_save,
    ("skill.search", 1): _skill_search,
    ("skill.activate", 1): _skill_activate,
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


def write_frame(
    stream: BinaryIO, kind: int, correlation: bytes, payload: bytes, *, flags: int = 0
) -> None:
    if len(correlation) != 16:
        raise ValueError("correlation id must be 16 bytes")
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("RMDY payload too large")
    header = (
        _MAGIC + struct.pack("<HHII", _PROTOCOL_VERSION, kind, flags, len(payload)) + correlation
    )
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
        return json.dumps({"ok": False, "error": f"unknown tool {tool_id}@{version}"}).encode(
            "utf-8"
        )
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
                {
                    "protocol": _PROTOCOL_VERSION,
                    "ready": True,
                    "capabilities": ["tools", "speech", "vision"],
                },
                separators=(",", ":"),
            ).encode("utf-8")
            write_frame(writer, _KIND_HEALTH, correlation, body)
            continue
        if kind == _KIND_TOOL_REQUEST:
            write_frame(writer, _KIND_TOOL_RESULT, correlation, _handle_tool(payload))
            continue
        err = f"unsupported RMDY frame kind {kind}".encode()
        write_frame(writer, _KIND_TOOL_RESULT, correlation, err, flags=1)


class _PipeFile:
    """Binary file-like over a Windows named-pipe HANDLE (CreateFileW)."""

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False

    def read(self, size: int = -1) -> bytes:
        if self._closed or size == 0:
            return b""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        remaining = 65536 if size < 0 else size
        chunks: list[bytes] = []
        while remaining > 0:
            to_read = min(65536, remaining)
            buf = (ctypes.c_char * to_read)()
            read = wintypes.DWORD(0)
            ok = kernel32.ReadFile(self._handle, buf, to_read, ctypes.byref(read), None)
            if not ok or read.value == 0:
                break
            chunks.append(buf.raw[: read.value])
            if size < 0:
                break
            remaining -= read.value
            if read.value < to_read:
                break
        return b"".join(chunks)

    def write(self, data: bytes) -> int:
        if self._closed:
            return 0
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        written_total = 0
        while written_total < len(data):
            chunk = data[written_total:]
            buf = (ctypes.c_char * len(chunk)).from_buffer_copy(chunk)
            written = wintypes.DWORD(0)
            ok = kernel32.WriteFile(
                self._handle,
                buf,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            if not ok:
                raise OSError("WriteFile failed on named pipe")
            written_total += int(written.value)
            if written.value == 0:
                break
        return written_total

    def flush(self) -> None:
        if self._closed:
            return
        import ctypes

        ctypes.windll.kernel32.FlushFileBuffers(self._handle)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        import ctypes

        ctypes.windll.kernel32.CloseHandle(self._handle)


def _dial_windows_pipe(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    generic_read = 0x80000000
    generic_write = 0x40000000
    open_existing = 3
    invalid_handle = ctypes.c_void_p(-1).value
    handle = kernel32.CreateFileW(
        endpoint,
        generic_read | generic_write,
        0,
        None,
        open_existing,
        0,
        None,
    )
    if handle in (None, 0, invalid_handle, -1):
        err = ctypes.GetLastError()
        raise OSError(f"CreateFileW({endpoint!r}) failed: Win32 {err}")
    pipe = _PipeFile(int(handle))
    stream = cast(BinaryIO, pipe)
    return stream, stream, pipe.close


def _dial_unix(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise OSError("AF_UNIX sockets are not available on this platform")
    sock = socket.socket(af_unix, socket.SOCK_STREAM)
    sock.connect(endpoint)
    reader = cast(BinaryIO, sock.makefile("rb", buffering=0))
    writer = cast(BinaryIO, sock.makefile("wb", buffering=0))

    def _close() -> None:
        try:
            reader.close()
        finally:
            try:
                writer.close()
            finally:
                sock.close()

    return reader, writer, _close


def dial_endpoint(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("empty RMDY endpoint")
    if endpoint.startswith("\\\\.\\pipe\\") or endpoint.startswith("//./pipe/"):
        # Normalize forward-slash form if a shell mangled it.
        normalized = endpoint.replace("/", "\\")
        return _dial_windows_pipe(normalized)
    return _dial_unix(endpoint)


def serve_endpoint(endpoint: str) -> None:
    import time

    last_err: Exception | None = None
    for _ in range(50):
        try:
            reader, writer, closer = dial_endpoint(endpoint)
            try:
                serve(reader, writer)
            finally:
                closer()
            return
        except OSError as exc:
            last_err = exc
            time.sleep(0.1)
    raise SystemExit(f"failed to dial RMDY endpoint {endpoint!r}: {last_err}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remedy.runtime.rmdy_tool_worker")
    parser.add_argument(
        "--endpoint",
        default="",
        help="IPC endpoint (named pipe / unix socket); default REMEDY_RMDY_ENDPOINT or stdio",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    endpoint = (args.endpoint or os.environ.get("REMEDY_RMDY_ENDPOINT") or "").strip()
    if endpoint:
        serve_endpoint(endpoint)
        return 0
    # Windows stdio is text by default; reopen as binary for RMDY frames.
    serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

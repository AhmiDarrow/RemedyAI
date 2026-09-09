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

The worker never logs to stdout (that is the wire). Tracebacks go to stderr
and to ``<REMEDY_HOME>/logs/rmdy_worker.log`` (rotating, 2 MB).

Concurrency
-----------
``KindToolRequest`` frames run on a thread pool (``_MAX_WORKERS``); responses
are written under one lock. Tools whose id starts with a ``_SERIAL_PREFIXES``
entry share the cached ``BasicRuntime`` (not thread-safe) and therefore run one
at a time under ``_serial_lock``. ``KindCancel`` marks the correlation id so its
late response is dropped instead of written.

Go-bound fields
---------------
``home_dir``, ``workspace_root`` and ``project_path`` in a tool input are only
honoured when the Go side vouches for them: either the request envelope carries
``"_go_bound": true`` (top-level, next to ``tool_id``) or the input object
itself carries ``"_go_bound": true``. Go's injection helper
(``httpapi.injectWorkspaceRoot`` and friends) sets the input-level field after
it has overwritten those paths with the session root; it must first delete any
model-supplied ``_go_bound``. Without the flag the worker strips those keys and
falls back to environment / config resolution, so a prompt-injected
``workspace_root`` can never retarget the jail.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, cast

logger = logging.getLogger("remedy.runtime.rmdy_tool_worker")

_PROTOCOL_VERSION = 1
_HEADER_SIZE = 32
_MAX_PAYLOAD = 16 << 20
_MAGIC = b"RMDY"
_READ_CHAR_CAP = 512_000
_MAX_WORKERS = 8
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 3

# Tools sharing the cached BasicRuntime run one at a time.
_SERIAL_PREFIXES = ("prompt.", "memory.", "skill.", "voice.", "vision.")
_serial_lock = threading.Lock()

# Envelope / input field the Go side sets after binding paths (see module doc).
GO_BOUND_FIELD = "_go_bound"
# Input keys ignored unless the request is Go-bound.
_GO_BOUND_ONLY_KEYS = ("home_dir", "workspace_root", "project_path")

# Packaged installs ship the third-party closure (pydantic, PyYAML, …) in a
# directory beside the zipapp because pydantic_core and _yaml are compiled
# extensions that cannot be imported from inside an archive. The Go launcher
# passes that directory here; scripts/build_rmdy_worker.py stages it.
_ENV_DEPS_DIR = "REMEDY_RMDY_DEPS"
# Third-party modules every RMDY handler chain needs (prompt.assemble pulls
# remedy.interfaces.config and remedy.models). Kept in step with
# DEPS_IMPORT_NAMES in scripts/build_rmdy_worker.py.
_REQUIRED_THIRD_PARTY = ("yaml", "pydantic", "pydantic_core")
# Distinct exit code the Go supervisor reports as a broken install
# (sysexits.h EX_CONFIG). Must match workers.ExitMissingDependencies.
EXIT_MISSING_DEPENDENCIES = 78

# Hidden test-only tools (never advertised) — enabled by REMEDY_RMDY_TEST_TOOLS=1.
_ENV_TEST_TOOLS = "REMEDY_RMDY_TEST_TOOLS"
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
_KIND_CANCEL = 6
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
    try:
        from remedy.core.security import refuse_protected_secret_path

        refuse_protected_secret_path(root)
    except ImportError:
        pass
    raw = (path or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        from remedy.core.security import refuse_protected_secret_path

        refuse_protected_secret_path(resolved)
    except ImportError:
        pass
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
    # ``offset`` is a 1-based line number (0 and 1 both mean "from the top").
    try:
        offset = max(1, int(inp.get("offset") or 1))
    except (TypeError, ValueError):
        offset = 1
    limit_raw = inp.get("limit")
    limit: int | None
    try:
        limit = None if limit_raw is None else max(1, int(limit_raw))
    except (TypeError, ValueError):
        limit = None
    number_lines = bool(inp.get("line_numbers") or False)

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    start = min(offset - 1, total_lines)
    end = total_lines if limit is None else min(total_lines, start + limit)
    window = lines[start:end]
    more_lines = end < total_lines

    if number_lines:
        width = max(1, len(str(end)))
        body = "".join(f"{start + i + 1:>{width}}\t{line}" for i, line in enumerate(window))
    else:
        body = "".join(window)
    truncated = more_lines
    if len(body) > _READ_CHAR_CAP:
        body = body[:_READ_CHAR_CAP]
        truncated = True
        # Chars, not lines, bounded the window: the caller should resume at
        # the last fully-included line rather than trust ``end``.
        included = body.count("\n")
        end = start + included
        more_lines = end < total_lines

    try:
        rel = str(target.relative_to(_workspace_root(inp)).as_posix())
    except ValueError:
        rel = str(target)
    out: dict[str, Any] = {
        "path": rel,
        "content": body,
        "total_lines": total_lines,
        "line_start": min(start + 1, max(total_lines, 1)),
        "line_end": end,
    }
    if truncated:
        out["truncated"] = True
    if more_lines:
        out["next_offset"] = end + 1
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
    if _is_credential_name(target.name) or any(_is_credential_name(p) for p in target.parts):
        raise PermissionError("credential-looking files are not editable")
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {path}")

    # Byte-exact round trip: keep the BOM and every CR/LF as found on disk.
    # ``Path.read_text`` would fold CRLF to LF and the rewrite would silently
    # re-line-end the whole file.
    raw_bytes = target.read_bytes()
    bom = b"\xef\xbb\xbf" if raw_bytes.startswith(b"\xef\xbb\xbf") else b""
    try:
        content = raw_bytes[len(bom) :].decode("utf-8")
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
        from remedy.core.atomic_json import write_bytes_atomic

        write_bytes_atomic(target, bom + result.new_content.encode("utf-8"))

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
    try:
        context = int(inp.get("context") or 0)
    except (TypeError, ValueError):
        context = 0
    context = max(0, min(5, context))

    root = _workspace_root(inp)
    # Keep absolute paths inside the workspace jail (fail closed).
    if path not in (".", "./", ""):
        _resolve_workspace_path(path, inp)

    from remedy.core.repo_search import is_capped_label, search_repo

    home = (os.environ.get("REMEDY_HOME") or "").strip() or None
    hits, engine = search_repo(
        root,
        pattern,
        path=path,
        glob=glob,
        max_matches=max_matches,
        case_insensitive=case_insensitive,
        context_before=context,
        context_after=context,
        home_dir=home,
        allowed_roots=[root],
        access_scope="project",
    )
    label = str(engine)
    if label.startswith("error:"):
        detail = label[len("error:") :].strip() or label
        if detail.startswith("invalid regex"):
            raise ValueError(detail)
        raise PermissionError(detail)
    matches: list[dict[str, Any]] = []
    for hit in hits:
        matches.append(
            {
                "path": str(hit.path),
                "line": int(hit.line),
                "text": str(hit.text),
            }
        )
    capped = is_capped_label(label)
    return {
        "pattern": pattern,
        "engine": label,
        "matches": matches,
        "total": len(matches),
        "capped": capped,
        "truncated": capped or ("truncated:" in label),
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


def _prompt_slim_epoch(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.prompt_assemble import slim_epoch

    return slim_epoch(inp)


def _prompt_should_continue(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.prompt_assemble import should_continue

    return should_continue(inp)


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


def _mail_list(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.mail_calendar_tools import mail_list

    return mail_list(inp)


def _mail_send(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.mail_calendar_tools import mail_send

    return mail_send(inp)


def _calendar_list_events(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.mail_calendar_tools import calendar_list_events

    return calendar_list_events(inp)


def _calendar_create_event(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.mail_calendar_tools import calendar_create_event

    return calendar_create_event(inp)


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
    ("prompt.slim_epoch", 1): _prompt_slim_epoch,
    ("prompt.should_continue", 1): _prompt_should_continue,
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
    ("mail.list", 1): _mail_list,
    ("mail.send", 1): _mail_send,
    ("calendar.list_events", 1): _calendar_list_events,
    ("calendar.create_event", 1): _calendar_create_event,
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


def _debug_sleep(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: block the calling pool thread for ``seconds`` (max 30)."""
    try:
        seconds = float(inp.get("seconds") or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = max(0.0, min(30.0, seconds))
    time.sleep(seconds)
    return {"slept": seconds}


def _debug_exit(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: raise SystemExit inside a tool (must never stop the worker)."""
    raise SystemExit(int(inp.get("code") or 3))


def _debug_big(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: return ``chars`` bytes of ASCII (exercise the frame limit)."""
    try:
        chars = int(inp.get("chars") or 0)
    except (TypeError, ValueError):
        chars = 0
    return {"blob": "x" * max(0, chars)}


_TEST_HANDLERS: dict[tuple[str, int], ToolHandler] = {
    ("debug.sleep", 1): _debug_sleep,
    ("debug.exit", 1): _debug_exit,
    ("debug.big", 1): _debug_big,
}


def _test_tools_enabled() -> bool:
    return (os.environ.get(_ENV_TEST_TOOLS) or "").strip().lower() in {"1", "true", "yes"}


def _lookup_handler(tool_id: str, version: int) -> ToolHandler | None:
    handler = _HANDLERS.get((tool_id, version))
    if handler is None and _test_tools_enabled():
        handler = _TEST_HANDLERS.get((tool_id, version))
    return handler


def _is_serial_tool(tool_id: str) -> bool:
    return tool_id.startswith(_SERIAL_PREFIXES)


def _error_payload(message: str, **extra: Any) -> bytes:
    body: dict[str, Any] = {"ok": False, "error": message}
    body.update(extra)
    return json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")


def _encode_result(output: Any) -> bytes:
    """Serialize a tool result; oversize results become an error payload."""
    encoded = json.dumps({"ok": True, "output": output}, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_PAYLOAD:
        return _error_payload("result exceeds 16 MiB frame limit", size=len(encoded))
    return encoded


def _is_go_bound(request: Mapping[str, Any], raw_input: Mapping[str, Any]) -> bool:
    return bool(request.get(GO_BOUND_FIELD)) or bool(raw_input.get(GO_BOUND_FIELD))


def _sanitize_input(request: Mapping[str, Any], raw_input: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the marker field and, unless Go-bound, the path-binding keys."""
    bound = _is_go_bound(request, raw_input)
    cleaned: dict[str, Any] = {}
    for key, value in raw_input.items():
        if key == GO_BOUND_FIELD:
            continue
        if not bound and key in _GO_BOUND_ONLY_KEYS:
            continue
        cleaned[key] = value
    return cleaned


def _handle_tool(payload: bytes) -> bytes:
    """Run one KindToolRequest. Never raises; the wire always gets a payload."""
    try:
        request = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _error_payload(f"invalid tool request: {exc}")
    if not isinstance(request, Mapping):
        return _error_payload("tool request must be a JSON object")
    tool_id = str(request.get("tool_id") or "")
    try:
        version = int(request.get("version") or 0)
    except (TypeError, ValueError):
        return _error_payload(f"invalid tool version {request.get('version')!r}")
    raw_input = request.get("input", {})
    if isinstance(raw_input, str):
        try:
            raw_input = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return _error_payload(f"invalid input: {exc}")
    if raw_input is None:
        raw_input = {}
    if not isinstance(raw_input, Mapping):
        return _error_payload("input must be a JSON object")
    handler = _lookup_handler(tool_id, version)
    if handler is None:
        return _error_payload(f"unknown tool {tool_id}@{version}")
    tool_input = _sanitize_input(request, raw_input)
    try:
        if _is_serial_tool(tool_id):
            with _serial_lock:
                output = handler(tool_input)
        else:
            output = handler(tool_input)
        return _encode_result(output)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 — SystemExit et al. must not stop the worker
        logger.exception("tool %s@%s failed", tool_id, version)
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, SystemExit):
            message = f"tool raised SystemExit({exc.code!r})"
        return _error_payload(message)


def _health_payload() -> bytes:
    return json.dumps(
        {
            "protocol": _PROTOCOL_VERSION,
            "ready": True,
            "capabilities": ["tools", "speech", "vision"],
        },
        separators=(",", ":"),
    ).encode("utf-8")


class _FrameWriter:
    """Serializes frame writes from the pool onto one stream."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self.broken = False

    def send(self, kind: int, correlation: bytes, payload: bytes, *, flags: int = 0) -> bool:
        with self._lock:
            if self.broken:
                return False
            try:
                write_frame(self._stream, kind, correlation, payload, flags=flags)
                return True
            except (OSError, ValueError):
                self.broken = True
                logger.exception("rmdy frame write failed; peer gone")
                return False


class _Dispatcher:
    """Thread-pool tool dispatch with KindCancel suppression."""

    def __init__(self, writer: _FrameWriter, max_workers: int = _MAX_WORKERS) -> None:
        self._writer = writer
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rmdy-tool")
        self._lock = threading.Lock()
        self._inflight: dict[bytes, Future[None]] = {}
        self._cancelled: set[bytes] = set()

    def submit(self, correlation: bytes, payload: bytes) -> None:
        with self._lock:
            if correlation in self._inflight:
                self._writer.send(
                    _KIND_TOOL_RESULT,
                    correlation,
                    _error_payload("duplicate active correlation ID"),
                )
                return
            future = self._pool.submit(self._run, correlation, payload)
            self._inflight[correlation] = future

    def cancel(self, correlation: bytes) -> None:
        with self._lock:
            if correlation in self._inflight:
                self._cancelled.add(correlation)

    def _run(self, correlation: bytes, payload: bytes) -> None:
        try:
            response = _handle_tool(payload)
        except BaseException:  # noqa: BLE001 — a pool thread must never die silently
            logger.exception("rmdy dispatcher failure")
            response = _error_payload("internal worker error")
        with self._lock:
            self._inflight.pop(correlation, None)
            suppressed = correlation in self._cancelled
            self._cancelled.discard(correlation)
        if suppressed:
            logger.info("dropping late response for cancelled request %s", correlation.hex())
            return
        self._writer.send(_KIND_TOOL_RESULT, correlation, response)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


def serve(reader: BinaryIO, writer: BinaryIO) -> None:
    """Serve frames until EOF on *reader*.

    Tool failures of any kind are answered on the wire and never end the
    loop; only stream EOF (or an unrecoverable protocol desync, which is
    logged) returns.
    """
    out = _FrameWriter(writer)
    dispatcher = _Dispatcher(out)
    try:
        while True:
            try:
                kind, _flags, correlation, payload = read_frame(reader)
            except EOFError:
                return
            except ValueError:
                logger.exception("rmdy protocol desync; closing stream")
                return
            except OSError:
                logger.exception("rmdy stream read failed")
                return
            if kind == _KIND_HEALTH:
                out.send(_KIND_HEALTH, correlation, _health_payload())
                continue
            if kind == _KIND_TOOL_REQUEST:
                dispatcher.submit(correlation, payload)
                continue
            if kind == _KIND_CANCEL:
                dispatcher.cancel(correlation)
                continue
            err = f"unsupported RMDY frame kind {kind}".encode()
            out.send(_KIND_TOOL_RESULT, correlation, err, flags=1)
    finally:
        dispatcher.shutdown()


def _log_dir() -> Path | None:
    try:
        from remedy.home import default_home

        home = default_home()
    except Exception:  # noqa: BLE001 — logging must never block startup
        raw = (os.environ.get("REMEDY_HOME") or "").strip()
        if not raw:
            return None
        home = Path(raw).expanduser()
    return home / "logs"


def bootstrap_dependency_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Put the packaged dependency directory last on ``sys.path``.

    Packaged installs run a managed CPython with only the standard library, so
    the launcher points ``REMEDY_RMDY_DEPS`` at the staged closure. Appending
    (never inserting) keeps a developer venv authoritative: the deps directory
    is purely additive and only answers imports nothing else provides.

    Runs before anything imports ``remedy.*``, so it must not log — the caller
    reports a configured-but-missing directory once logging is up.
    """
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_DEPS_DIR) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir():
        return None
    entry = str(path)
    if entry not in sys.path:
        sys.path.append(entry)
    return path


def verify_dependencies() -> None:
    """Fail fast and legibly when the third-party closure is not importable.

    Without this the first ``prompt.assemble`` call — the Go attach probe —
    dies deep inside ``remedy.interfaces.config`` and the operator sees a tool
    failure rather than a broken install.
    """
    import importlib

    for name in _REQUIRED_THIRD_PARTY:
        try:
            importlib.import_module(name)
        except Exception:
            deps = (os.environ.get(_ENV_DEPS_DIR) or "").strip() or "<unset>"
            logger.exception(
                "rmdy worker cannot import required dependency %r "
                "(%s=%s, executable=%s); the install is incomplete",
                name,
                _ENV_DEPS_DIR,
                deps,
                sys.executable,
            )
            logging.shutdown()
            raise SystemExit(EXIT_MISSING_DEPENDENCIES) from None


def configure_logging() -> Path | None:
    """stderr + rotating ``<REMEDY_HOME>/logs/rmdy_worker.log``; returns the log path."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not any(getattr(h, "_rmdy_stderr", False) for h in root.handlers):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(fmt)
        stderr_handler._rmdy_stderr = True  # type: ignore[attr-defined]
        root.addHandler(stderr_handler)
    log_dir = _log_dir()
    if log_dir is None:
        return None
    path = log_dir / "rmdy_worker.log"
    if any(getattr(h, "_rmdy_file", None) == str(path) for h in root.handlers):
        return path
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
        )
    except OSError:
        logger.warning("cannot open worker log at %s", path)
        return None
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)
    file_handler._rmdy_file = str(path)  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    return path


_ERROR_IO_PENDING = 997
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_OPERATION_ABORTED = 995
_FILE_FLAG_OVERLAPPED = 0x40000000
_win32_lock = threading.Lock()
_win32_cache: dict[str, Any] = {}


def _win32_last_error() -> int:
    """Win32 GetLastError via ctypes.

    Declared through ``getattr`` because ``ctypes.get_last_error`` is absent
    from the non-Windows typeshed stubs the repo type-checks against.
    """
    import ctypes

    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    return int(getter())


def _win32_kernel32() -> Any:
    """kernel32 with ``use_last_error`` — without assuming Linux stubs have WinDLL."""
    with _win32_lock:
        cached = _win32_cache.get("kernel32")
        if cached is not None:
            return cached
        import ctypes

        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("ctypes.WinDLL is only available on Windows")
        kernel32 = win_dll("kernel32", use_last_error=True)
        # Explicit HANDLE prototypes: a bare Python int would be narrowed to a
        # C int and 64-bit handle values could be truncated.
        from ctypes import wintypes

        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        for fn in (kernel32.ReadFile, kernel32.WriteFile):
            fn.restype = wintypes.BOOL
            fn.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
        kernel32.GetOverlappedResult.restype = wintypes.BOOL
        kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _win32_cache["kernel32"] = kernel32
        return kernel32


def _win32_overlapped_type() -> Any:
    with _win32_lock:
        cached = _win32_cache.get("OVERLAPPED")
        if cached is not None:
            return cached
        import ctypes
        from ctypes import wintypes

        class _Overlapped(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_void_p),
                ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            ]

        _win32_cache["OVERLAPPED"] = _Overlapped
        return _Overlapped


class _PipeFile:
    """Binary file-like over a Windows named-pipe HANDLE (CreateFileW).

    The handle is opened with ``FILE_FLAG_OVERLAPPED`` and every operation
    carries its own OVERLAPPED + event. A synchronous pipe handle serializes
    I/O per file object, so a pool thread's WriteFile would block behind the
    reader thread's pending ReadFile and the worker would deadlock waiting
    for a request that Go only sends after our response.
    """

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False

    def _overlapped_io(self, op: str, buf: Any, size: int) -> int:
        """Run ReadFile/WriteFile with a private OVERLAPPED; returns bytes moved."""
        import ctypes
        from ctypes import wintypes

        kernel32 = _win32_kernel32()
        overlapped_type = _win32_overlapped_type()
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise OSError(f"CreateEventW failed: Win32 {_win32_last_error()}")
        try:
            ov = overlapped_type()
            ov.hEvent = event
            moved = wintypes.DWORD(0)
            fn = kernel32.ReadFile if op == "read" else kernel32.WriteFile
            ok = fn(self._handle, buf, size, ctypes.byref(moved), ctypes.byref(ov))
            if not ok:
                err = _win32_last_error()
                if err in (_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED):
                    return 0
                if err != _ERROR_IO_PENDING:
                    raise OSError(f"{op} failed on named pipe: Win32 {err}")
            done = wintypes.DWORD(0)
            if not kernel32.GetOverlappedResult(
                self._handle, ctypes.byref(ov), ctypes.byref(done), True
            ):
                err = _win32_last_error()
                if err in (
                    _ERROR_BROKEN_PIPE,
                    _ERROR_PIPE_NOT_CONNECTED,
                    _ERROR_OPERATION_ABORTED,
                ):
                    return 0
                raise OSError(f"{op} failed on named pipe: Win32 {err}")
            return int(done.value)
        finally:
            kernel32.CloseHandle(event)

    def read(self, size: int = -1) -> bytes:
        if self._closed or size == 0:
            return b""
        import ctypes

        to_read = 65536 if size < 0 else min(65536, size)
        buf = (ctypes.c_char * to_read)()
        got = self._overlapped_io("read", buf, to_read)
        if got <= 0:
            return b""
        return buf.raw[:got]

    def write(self, data: bytes) -> int:
        if self._closed:
            return 0
        import ctypes

        written_total = 0
        while written_total < len(data):
            chunk = data[written_total:]
            buf = (ctypes.c_char * len(chunk)).from_buffer_copy(chunk)
            written = self._overlapped_io("write", buf, len(chunk))
            if written <= 0:
                raise OSError("WriteFile on named pipe wrote nothing (peer closed)")
            written_total += written
        return written_total

    def flush(self) -> None:
        # Byte-mode pipe writes are visible to the peer once WriteFile
        # completes; FlushFileBuffers would block until Go drained the pipe.
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _win32_kernel32().CloseHandle(self._handle)


def _dial_windows_pipe(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    import ctypes

    kernel32 = _win32_kernel32()
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
        _FILE_FLAG_OVERLAPPED,
        None,
    )
    if handle in (None, 0, invalid_handle, -1):
        raise OSError(f"CreateFileW({endpoint!r}) failed: Win32 {_win32_last_error()}")
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
    last_err: Exception | None = None
    for _ in range(50):
        try:
            reader, writer, closer = dial_endpoint(endpoint)
        except OSError as exc:
            last_err = exc
            time.sleep(0.1)
            continue
        logger.info("rmdy worker attached to %s (pid=%d)", endpoint, os.getpid())
        try:
            serve(reader, writer)
        finally:
            closer()
        logger.info("rmdy worker stream closed; exiting")
        return
    raise SystemExit(f"failed to dial RMDY endpoint {endpoint!r}: {last_err}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remedy.runtime.rmdy_tool_worker")
    parser.add_argument(
        "--endpoint",
        default="",
        help="IPC endpoint (named pipe / unix socket); default REMEDY_RMDY_ENDPOINT or stdio",
    )
    args = parser.parse_args(argv)
    # sys.path first: nothing may import remedy.* before the packaged
    # dependency directory is in place. Logging follows, then the preflight.
    deps_dir = bootstrap_dependency_path()
    configure_logging()
    configured = (os.environ.get(_ENV_DEPS_DIR) or "").strip()
    if configured and deps_dir is None:
        logger.warning("%s points at a missing directory: %s", _ENV_DEPS_DIR, configured)
    verify_dependencies()
    endpoint = (args.endpoint or os.environ.get("REMEDY_RMDY_ENDPOINT") or "").strip()
    if endpoint:
        serve_endpoint(endpoint)
        return 0
    # Windows stdio is text by default; reopen as binary for RMDY frames.
    serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

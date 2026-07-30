"""Session work-root memory — trees actually touched this turn/session.

Focus folder is optional. When tools resolve paths under other repos, we
remember those roots so orientation/fingerprint can follow real work.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

_MAX_ROOTS = 4

_MARKER_FILES = (
    "project.godot",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "AGENTS.md",
    ".git",
    "Makefile",
)


def _is_work_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    for name in _MARKER_FILES:
        try:
            if (path / name).exists():
                return True
        except OSError:
            continue
    return False


def discover_work_root(path: Path | str | None) -> Path | None:
    """Walk up from *path* looking for a project-like root (max 8 levels)."""
    if path is None:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        try:
            p = Path(path).expanduser().absolute()
        except Exception:
            return None
    if p.is_file():
        p = p.parent
    cur = p
    for _ in range(10):
        if _is_work_root(cur):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # Fall back to the directory itself if it exists
    return p if p.is_dir() else None


def note_work_path(runtime: Any, path: Path | str | None) -> Path | None:
    """Record a touched path's work root on *runtime*. Returns root if found.

    Also mirrors into the session-scoped work-roots cache when a session is bound.
    """
    root = discover_work_root(path)
    if root is None:
        return None
    try:
        key = str(root.resolve())
    except OSError:
        key = str(root)
    roots: list[str] = list(getattr(runtime, "_work_roots", None) or [])
    # Move to front; cap
    roots = [r for r in roots if r.lower() != key.lower()]
    roots.insert(0, key)
    runtime._work_roots = roots[:_MAX_ROOTS]
    # Keep session cache in sync so tab switches restore the right set
    with suppress(Exception):
        from remedy.core.session_continuity import _work_roots_by_session, _trim_cache

        sid = str(getattr(runtime, "_session_id", "") or "").strip()
        if sid:
            _work_roots_by_session[sid] = list(runtime._work_roots)
            _trim_cache(_work_roots_by_session)
    return root


def get_work_roots(runtime: Any) -> list[Path]:
    out: list[Path] = []
    for r in list(getattr(runtime, "_work_roots", None) or []):
        try:
            p = Path(r)
            if p.is_dir():
                out.append(p)
        except OSError:
            continue
    return out


def work_roots_context_block(runtime: Any, *, max_chars: int = 3_200) -> str:
    """Orientation + fingerprint for active work roots (beyond focus if needed)."""
    from remedy.core.project_fingerprint import fingerprint_path, orientation_block

    parts: list[str] = []
    seen: set[str] = set()
    # Focus first
    try:
        focus = runtime.effective_project_path()
        key = str(focus.resolve()).lower()
        seen.add(key)
    except Exception:
        focus = None

    roots = get_work_roots(runtime)
    # Prefer work roots that are not the focus (extra signal)
    ordered: list[Path] = []
    if focus is not None:
        ordered.append(focus)
    for r in roots:
        try:
            k = str(r.resolve()).lower()
        except OSError:
            k = str(r).lower()
        if k not in seen:
            seen.add(k)
            ordered.append(r)

    budget = max_chars
    for root in ordered[:_MAX_ROOTS]:
        chunk_parts: list[str] = []
        try:
            orient = orientation_block(root, max_chars=min(1_200, budget))
            if orient:
                chunk_parts.append(orient)
            fp = fingerprint_path(root)
            lines = fp.context_lines()
            if lines:
                chunk_parts.append("\n".join(lines))
        except Exception:
            continue
        if not chunk_parts:
            continue
        header = f"## Active work tree\n{root}"
        block = header + "\n" + "\n".join(chunk_parts)
        if len(block) > budget:
            block = block[: budget - 1] + "…"
        parts.append(block)
        budget -= len(block)
        if budget < 200:
            break
    return "\n\n".join(parts)

"""Project workspace root for agent sessions (OpenCode-style folder context).

The configured / session ``project_path`` is the default directory for file
tools, shell cwd, and @file UI jailing — similar to opening a folder in a
code agent.
"""

from __future__ import annotations

import os
from pathlib import Path

from remedy.core.errors import SecurityError
from remedy.core.security import safe_path

# Skip noise when listing a workspace root for the agent system prompt.
_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".idea",
    ".vs",
    "target",
}


def resolve_project_path(raw: str | None, *, fallback: Path | None = None) -> Path:
    """Resolve a project path to an absolute directory.

    Empty / '.' / missing → ``fallback`` or ``Path.cwd()``.
    Creates the directory if it does not exist when it looks intentional.
    """
    fb = (fallback or Path.cwd()).expanduser().resolve()
    if raw is None:
        return fb
    text = str(raw).strip()
    if not text or text in (".", "./"):
        return fb
    path = Path(text).expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = Path(text).expanduser().absolute()
    return path


def ensure_project_dir(path: Path) -> Path:
    """Ensure the project path exists as a directory; return resolved path."""
    path = path.expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise SecurityError(
            f"Project path is not a directory: {path}",
            rule="project_not_dir",
            detail={"path": str(path)},
        )
    return path


def jail_path(user_path: str, project_root: Path) -> Path:
    """Resolve ``user_path`` under ``project_root`` (blocks traversal)."""
    root = ensure_project_dir(project_root)
    if not user_path or user_path in (".", "./"):
        return root
    # Absolute paths must still stay under root
    candidate = Path(user_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError as err:
            raise SecurityError(
                f"Path outside project workspace: {user_path}",
                rule="path_traversal",
                detail={"input": user_path, "root": str(root)},
            ) from err
    return safe_path(user_path, base_dir=root)


def list_workspace_entries(project_root: Path, *, limit: int = 40) -> list[dict[str, str]]:
    """Top-level files/dirs for agent context (name + type)."""
    root = ensure_project_dir(project_root)
    entries: list[dict[str, str]] = []
    try:
        for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith("."):
                continue
            if p.name in _SKIP_DIR_NAMES:
                continue
            entries.append(
                {
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                }
            )
            if len(entries) >= limit:
                break
    except OSError:
        pass
    return entries


def workspace_context_block(project_root: Path) -> str:
    """Markdown-ish block for the agent system prompt."""
    try:
        root = ensure_project_dir(project_root)
    except Exception as exc:
        return f"Working directory: (unavailable: {exc})"
    lines = [
        f"Working directory (project root): {root}",
        "All file and shell tools are jailed to this directory unless stated otherwise.",
        "Prefer relative paths from this root.",
    ]
    entries = list_workspace_entries(root)
    if entries:
        listing = ", ".join(
            f"{e['name']}/" if e["type"] == "dir" else e["name"] for e in entries
        )
        lines.append(f"Top-level: {listing}")
    else:
        lines.append("Top-level: (empty or unreadable)")
    return "\n".join(lines)


def default_project_from_config(cfg: dict | None) -> Path:
    """Pick project path from config dict / env / cwd."""
    cfg = cfg or {}
    env = os.environ.get("REMEDY_PROJECT_PATH") or os.environ.get("REMEDY_FILES_ROOT") or ""
    raw = cfg.get("project_path") or env or None
    return resolve_project_path(raw if raw else None)

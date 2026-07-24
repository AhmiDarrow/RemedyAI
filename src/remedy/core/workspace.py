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
    return resolve_under_roots(user_path, [project_root])


def normalize_access_scope(raw: str | None) -> str:
    """Return project | home | full."""
    s = (raw or "project").strip().lower()
    if s in ("home", "user", "project+home", "project_home"):
        return "home"
    if s in ("full", "machine", "all", "unrestricted"):
        return "full"
    return "project"


def allowed_roots_for_scope(
    scope: str,
    project_root: Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Roots the agent may touch for the given access scope."""
    roots: list[Path] = []
    try:
        roots.append(ensure_project_dir(project_root))
    except Exception:
        roots.append(resolve_project_path(str(project_root)))
    scope = normalize_access_scope(scope)
    if scope in ("home", "full"):
        h = (home or Path.home()).expanduser()
        try:
            h = h.resolve()
        except OSError:
            h = h.absolute()
        if h not in roots:
            roots.append(h)
    # full: roots still list project + home for cwd defaults; absolute paths
    # under the user's OS permissions are allowed via resolve_under_roots.
    return roots


def resolve_under_roots(
    user_path: str,
    roots: list[Path],
    *,
    access_scope: str = "project",
) -> Path:
    """Resolve a path that must stay under one of *roots* (or full-user on full).

    ``access_scope=full`` allows any absolute path the process can resolve
    under the current user (still no silent admin elevation).
    """
    scope = normalize_access_scope(access_scope)
    if not roots:
        roots = [Path.cwd()]
    primary = roots[0]
    if not user_path or user_path in (".", "./"):
        return ensure_project_dir(primary)

    candidate = Path(user_path).expanduser()
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if scope == "full":
            # Block a few clearly dangerous locations
            parts_lower = {p.lower() for p in resolved.parts}
            if any(x in parts_lower for x in ("$recycle.bin", "system volume information")):
                raise SecurityError(
                    f"Path not allowed: {user_path}",
                    rule="path_denied",
                    detail={"input": user_path},
                )
            return resolved
        for root in roots:
            try:
                r = ensure_project_dir(root) if root.exists() else root.resolve()
            except Exception:
                try:
                    r = root.resolve()
                except OSError:
                    r = root.absolute()
            try:
                resolved.relative_to(r)
                return resolved
            except ValueError:
                continue
        raise SecurityError(
            f"Path outside allowed roots ({scope}): {user_path}",
            rule="path_traversal",
            detail={
                "input": user_path,
                "roots": [str(r) for r in roots],
                "scope": scope,
            },
        )

    # Relative: try each root; prefer first root that exists
    last_err: Exception | None = None
    for root in roots:
        try:
            return safe_path(user_path, base_dir=root)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return safe_path(user_path, base_dir=primary)


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


def workspace_context_block(
    project_root: Path,
    *,
    access_scope: str = "project",
    extra_roots: list[Path] | None = None,
) -> str:
    """Markdown-ish block for the agent system prompt."""
    try:
        root = ensure_project_dir(project_root)
    except Exception as exc:
        return f"Working directory: (unavailable: {exc})"
    scope = normalize_access_scope(access_scope)
    lines = [
        f"Working directory (project root): {root}",
        f"Access scope: {scope}",
    ]
    if scope == "project":
        lines.append(
            "File and shell tools are jailed to the project directory unless the user "
            "raises access scope in Settings."
        )
    elif scope == "home":
        lines.append(
            "File tools may use the project directory and the user home profile. "
            "Prefer the project root for code work."
        )
    else:
        lines.append(
            "Access scope is full user machine (no silent admin elevation). "
            "Prefer reversible actions; confirm destructive ops."
        )
    if extra_roots:
        lines.append("Allowed roots: " + ", ".join(str(r) for r in extra_roots[:6]))
    lines.append("Prefer relative paths from the project root when possible.")
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

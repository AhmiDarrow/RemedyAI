"""Project workspace root for agent sessions (folder context).

The configured / session ``project_path`` is the default directory for file
tools, shell cwd, and @file UI jailing.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from remedy.core.errors import SecurityError
from remedy.core.security import refuse_protected_secret_path, safe_path

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


def is_unset_project_path(raw: str | Path | None) -> bool:
    """True when the user has not chosen a real project folder.

    Empty / missing / ``.`` means “no project” — not “current process cwd”.
    """
    if raw is None:
        return True
    text = str(raw).strip()
    return not text or text in (".", "./")


def resolve_project_path(raw: str | None, *, fallback: Path | None = None) -> Path:
    """Resolve a project path to an absolute directory.

    Empty / '.' / missing → ``fallback`` or the user home directory (not the
    process cwd — Desktop sidecars often run from the install folder).
    Pair with :func:`effective_access_scope`: unset project → full access.
    """
    if fallback is not None:
        fb = fallback.expanduser().resolve()
    else:
        try:
            fb = Path.home().expanduser().resolve()
        except OSError:
            fb = Path.cwd().resolve()
    if is_unset_project_path(raw):
        return fb
    text = str(raw).strip()
    path = Path(text).expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = Path(text).expanduser().absolute()
    return path


def effective_access_scope(
    configured: str | None,
    project_path_raw: str | Path | None,
) -> str:
    """Access scope used for tools.

    When no project folder is set, treat as **full** user-machine access so
    the partner is not jailed to an install/cwd folder. Prefer picking a
    project folder for focused coding work.
    """
    if is_unset_project_path(project_path_raw):
        return "full"
    return normalize_access_scope(configured)


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
    """Return project | home | full | untrusted.

    ``untrusted`` — project root only (no Desktop/Documents/Downloads auto-roots);
    pair with Ask approvals for downloaded/untrusted folders.
    """
    s = (raw or "project").strip().lower()
    if s in ("home", "user", "project+home", "project_home"):
        return "home"
    if s in ("full", "machine", "all", "unrestricted"):
        return "full"
    if s in ("untrusted", "sandbox", "strict", "download"):
        return "untrusted"
    return "project"


def user_profile_work_folders(*, home: Path | None = None) -> list[Path]:
    """Desktop / Documents / Downloads — common *read/research* targets.

    Included under non-untrusted **read** roots so the agent can open notes,
    downloads, and desktop files without raising access scope. Writes still
    use :func:`write_roots_for_scope` (project-only under ``project`` scope).
    """
    h = (home or Path.home()).expanduser()
    try:
        h = h.resolve()
    except OSError:
        h = h.absolute()
    out: list[Path] = []
    for name in ("Desktop", "Documents", "Downloads"):
        p = h / name
        try:
            if p.is_dir():
                out.append(p.resolve())
        except OSError:
            if p.exists():
                out.append(p.absolute())
    return out


def _primary_project_root(project_root: Path) -> Path:
    try:
        return ensure_project_dir(project_root)
    except Exception:
        return resolve_project_path(str(project_root))


def _home_root(*, home: Path | None = None) -> Path:
    h = (home or Path.home()).expanduser()
    try:
        return h.resolve()
    except OSError:
        return h.absolute()


def allowed_roots_for_scope(
    scope: str,
    project_root: Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Roots the agent may **read / research** under for the given access scope.

    Under ``project`` scope this includes the focus folder plus Desktop /
    Documents / Downloads when present (view-only convenience). Mutations use
    :func:`write_roots_for_scope` instead — project scope writes stay in the
    project folder only.
    """
    roots: list[Path] = [_primary_project_root(project_root)]
    scope = normalize_access_scope(scope)
    # Untrusted: project root only (no Desktop/Documents/Downloads).
    if scope != "untrusted":
        for folder in user_profile_work_folders(home=home):
            if folder not in roots:
                roots.append(folder)
    if scope in ("home", "full"):
        h = _home_root(home=home)
        if h not in roots:
            roots.append(h)
    # F1 / owner's manual — always readable (not writable). Fixes agents claiming
    # in-app help is "outside access scope" when project ≠ RemedyAI repo.
    try:
        from remedy.core.help_docs import help_read_roots

        for hr in help_read_roots():
            if hr not in roots:
                roots.append(hr)
    except Exception:
        pass
    # full: roots still list project + home for cwd defaults; absolute paths
    # under the user's OS permissions are allowed via resolve_under_roots.
    return roots


def write_roots_for_scope(
    scope: str,
    project_root: Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Roots the agent may **create / edit / shell-cwd** under.

    When a real project folder is set, mutations stay inside the focus tree:

    - ``project`` / ``untrusted`` / ``full`` → **project root only**.
      ``full`` still expands *read* roots and absolute *reads*; it must not
      defeat the project write jail (view/research outside is OK; edits
      outside are not).
    - ``home`` → project + user home (intentional multi-folder edits).

    Profile work folders (Desktop/Documents/Downloads) are **never** write
    roots while a project is bound — use relative paths under the project
    or raise scope to ``home`` only when home-wide edits are intended.
    """
    primary = _primary_project_root(project_root)
    scope = normalize_access_scope(scope)
    if scope == "home":
        roots: list[Path] = [primary]
        h = _home_root(home=home)
        if h not in roots:
            roots.append(h)
        return roots
    # project | untrusted | full (with a project bound) → project only
    return [primary]


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
        out = ensure_project_dir(primary)
        refuse_protected_secret_path(out)
        return out

    candidate = Path(user_path).expanduser()
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        # Always refuse auth secrets — including under access_scope=full and
        # when a junction/symlink resolves into ~/.remedy/auth.
        refuse_protected_secret_path(resolved)
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
    project_unset: bool = False,
) -> str:
    """Markdown-ish block for the agent system prompt."""
    try:
        root = ensure_project_dir(project_root)
    except Exception as exc:
        return f"Working directory: (unavailable: {exc})"
    scope = normalize_access_scope(access_scope)
    if project_unset:
        # Empty project path → full access (owner PC); recommend setting a folder.
        scope = "full"
    lines = [
        f"Default working directory (focus): {root}",
        f"Access scope: {scope}",
    ]
    if project_unset:
        lines.append(
            "No focus folder is set — default cwd is the user home profile; "
            "access is **full** for this account. "
            "Use absolute paths for any tree you work on; relative paths resolve "
            "from the default cwd. A focus folder is optional convenience, not required."
        )
    elif scope == "project":
        lines.append(
            "Relative paths resolve under the focus folder. You may **read/list** "
            "Desktop/Documents/Downloads for research, but **file_write / file_edit "
            "and shell workdir stay inside the focus folder only**. Raise access "
            "scope in Settings (home/full) only when intentional multi-tree edits "
            "are needed."
        )
    elif scope == "home":
        lines.append(
            "Focus folder plus user home profile are allowed for edits. "
            "Absolute paths are fine; relative paths resolve from the focus folder."
        )
    elif scope == "untrusted":
        lines.append(
            "Untrusted scope: focus folder only for reads and writes; "
            "high-impact tools stay on Ask."
        )
    else:
        lines.append(
            "Access scope is full for **reads** across the user machine "
            "(no silent admin elevation). **Writes/edits and shell workdir "
            "still stay inside the focus folder** when one is set — raise "
            "scope to home only for intentional home-wide edits."
        )
    if extra_roots:
        lines.append("Read roots: " + ", ".join(str(r) for r in extra_roots[:6]))
    if project_unset:
        lines.append(
            "Coding without a focus folder is first-class — list_dir / repo_search / "
            "file_* with absolute paths anywhere in scope."
        )
    else:
        lines.append(
            "Prefer relative paths under the focus folder for edits. Absolute paths "
            "are OK for reading other trees in read scope; do not write outside "
            "the project unless access scope is home/full."
        )
    entries = list_workspace_entries(root)
    if entries:
        listing = ", ".join(
            f"{e['name']}/" if e["type"] == "dir" else e["name"] for e in entries
        )
        lines.append(f"Top-level: {listing}")
    else:
        lines.append("Top-level: (empty or unreadable)")
    with suppress(Exception):
        from remedy.execution.host.stretch import format_home_line

        line = format_home_line()
        if line:
            lines.append(line)
    with suppress(Exception):
        from remedy.execution.host.dialect import format_dialect_line

        dline = format_dialect_line()
        if dline and dline not in "\n".join(lines):
            lines.append(dline)
    return "\n".join(lines)


def new_project_dir() -> Path:
    """Default first-run sandbox folder (guardrails without jailing to install dir)."""
    # Prefer Documents on Windows; fall back to ~/.remedy/projects
    try:
        docs = Path.home() / "Documents" / "Remedy Projects" / "New Project"
        return docs
    except OSError:
        return Path.home() / ".remedy" / "projects" / "New Project"


def ensure_new_project_seed() -> Path:
    """Create the New Project folder if missing (first-run helper only).

    Do **not** call this for every new session — sessions without a project are root.
    """
    return ensure_project_dir(new_project_dir())


def default_project_from_config(cfg: dict | None) -> Path:
    """Pick project path from config dict / env, else user home (not process cwd).

    Unset project → home path for tools that need *a* root; access_scope becomes
    **full** via :func:`effective_access_scope`. New Project is only for first-run
    config seeding, not an implicit every-session workspace.
    """
    cfg = cfg or {}
    env = os.environ.get("REMEDY_PROJECT_PATH") or os.environ.get("REMEDY_FILES_ROOT") or ""
    raw = cfg.get("project_path") or env or None
    if is_unset_project_path(raw):
        return resolve_project_path(None)  # home, never install cwd
    return resolve_project_path(str(raw))

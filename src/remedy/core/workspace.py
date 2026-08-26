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
from remedy.home import default_home

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


def is_volume_root_path(raw: str | Path | None) -> bool:
    """True for drive / filesystem roots (``C:\\``, ``C:``, ``/``).

    Those are not project folders — treating them as one made the sidebar
    grow a ``C:`` bucket and the files jail clamp to home anyway.
    """
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    if text in ("/", "\\"):
        return True
    if len(text) <= 3 and text[0].isalpha() and text[1:2] == ":":
        rest = text[2:].strip("\\/")
        if not rest:
            return True
    try:
        path = Path(text).expanduser().resolve()
    except OSError:
        try:
            path = Path(text).expanduser().absolute()
        except OSError:
            return False
    return path.parent == path


_OS_PROJECT_ROOTS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
    "recovery",
    "system volume information",
}
_POSIX_PROJECT_PREFIXES = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/root",
)


def windows_drive_os_kind(raw: str) -> str | None:
    """Classify a Windows drive path without resolving (works on POSIX CI).

    ``root`` is ``C:\\``. ``os`` is ``C:\\Windows\\...``. Else ``None``.
    """
    t = str(raw).strip()
    if t.startswith("\\\\?\\"):
        t = t[4:]
    t = t.replace("/", "\\")
    if len(t) < 2 or not t[0].isalpha() or t[1] != ":":
        return None
    rest = t[2:].lstrip("\\")
    if not rest:
        return "root"
    first = rest.split("\\", 1)[0].lower()
    if first in _OS_PROJECT_ROOTS:
        return "os"
    return None


def is_forbidden_project_path(raw: str | Path | None) -> bool:
    """True for OS / secrets trees that must never become the project root.

    Same-user owner can still pick them on disk; the agent jail must not.
    """
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    if windows_drive_os_kind(text) == "os":
        return True
    try:
        path = Path(text).expanduser().resolve()
    except OSError:
        try:
            path = Path(text).expanduser().absolute()
        except OSError:
            return False
    parts = [p.lower() for p in path.parts]
    if len(parts) >= 2 and parts[1] in _OS_PROJECT_ROOTS:
        return True
    posix = path.as_posix().lower()
    raw_posix = text.replace("\\", "/").lower()
    return any(
        candidate == prefix or candidate.startswith(prefix + "/")
        for candidate in (posix, raw_posix)
        for prefix in _POSIX_PROJECT_PREFIXES
    )


def is_unset_project_path(raw: str | Path | None) -> bool:
    """True when the user has not chosen a real project folder.

    Empty / missing / ``.`` / a volume root means “no project” — not cwd.
    """
    if raw is None:
        return True
    text = str(raw).strip()
    return not text or text in (".", "./") or is_volume_root_path(text)


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

    The owner's configured ``access_scope`` is authoritative for writes:

    - ``project`` / ``untrusted`` → **project root only**.
    - ``home`` → project + user home (intentional multi-folder edits).
    - ``full`` → project + user home as the *listed* roots; absolute paths
      anywhere the OS user may write are additionally allowed by
      :func:`resolve_under_roots(access_scope="full")` and by the shell jail
      (``access_scope="full"`` is machine-wide, the same as Approvals → Full).

    Profile work folders (Desktop/Documents/Downloads) are **not** write
    roots under ``project`` scope — use relative paths under the project, or
    raise scope to ``home`` / ``full`` when edits outside are intended.
    Auth secrets and Remedy's own installed runtime are refused at every scope.
    """
    primary = _primary_project_root(project_root)
    scope = normalize_access_scope(scope)
    if scope in ("home", "full"):
        roots: list[Path] = [primary]
        h = _home_root(home=home)
        if h not in roots:
            roots.append(h)
        return roots
    # project | untrusted → project only
    return [primary]


def _remedy_home_for_runtime_check() -> Path | None:
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        return None


def is_remedy_installed_code_path(path: Path | str | None) -> bool:
    """True when *path* is Remedy's own installed code (never a tool write target).

    Covers the managed voice runtime (``<REMEDY_HOME>/voice/runtime/**`` and
    any ``…/.remedy/voice/runtime/**``), the frozen sidecar itself and its
    ``_internal`` tree (``sys.executable`` when frozen) and a PyInstaller
    ``_MEIPASS`` extract. Reads are fine; patching the installed app from a session is not
    (the session's project is the place to edit code).
    """
    if path is None:
        return False
    try:
        p = Path(path).expanduser()
        try:
            p = p.resolve(strict=False)
        except (OSError, RuntimeError):
            p = p.absolute()
    except (TypeError, ValueError, RuntimeError):
        return False
    parts = [str(x).lower() for x in p.parts]
    for i in range(len(parts) - 2):
        if parts[i] == ".remedy" and parts[i + 1] == "voice" and parts[i + 2] == "runtime":
            return True
    # A Windows path string on POSIX is one Path part; still recognise the tree.
    raw = str(path).replace("/", "\\")
    if "\\" in raw:
        win_parts = [x.lower() for x in raw.split("\\") if x]
        for i in range(len(win_parts) - 2):
            if (
                win_parts[i] == ".remedy"
                and win_parts[i + 1] == "voice"
                and win_parts[i + 2] == "runtime"
            ):
                return True
    candidates: list[Path] = []
    rh = _remedy_home_for_runtime_check()
    if rh is not None:
        candidates.append(rh / "voice" / "runtime")
    import sys as _sys

    if getattr(_sys, "frozen", False) and _sys.executable:
        # PyInstaller onedir: the exe itself + its ``_internal`` tree (not the
        # whole folder — a project may legitimately sit beside the exe).
        exe = Path(_sys.executable)
        candidates.append(exe)
        candidates.append(exe.parent / "_internal")
    meipass = getattr(_sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(str(meipass)))
    for c in candidates:
        try:
            cr = c.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            cr = c.expanduser().absolute()
        try:
            if p == cr or p.is_relative_to(cr):
                return True
        except (ValueError, TypeError):
            continue
    return False


def refuse_remedy_installed_code_path(path: Path | str | None) -> None:
    """Raise a clear SecurityError for writes into Remedy's own installed code."""
    if is_remedy_installed_code_path(path):
        raise SecurityError(
            "That's Remedy's own installed code (frozen sidecar / managed voice "
            "runtime). It is never writable from a session at any access scope "
            "or approval mode — edit the project tree instead; the installed "
            f"copy is read-only: {path}",
            rule="remedy_installed_code",
            detail={"path": str(path)},
        )


def resolve_under_roots(
    user_path: str,
    roots: list[Path],
    *,
    access_scope: str = "project",
    for_write: bool = False,
) -> Path:
    """Resolve a path that must stay under one of *roots* (or full-user on full).

    ``access_scope=full`` allows any absolute path the process can resolve
    under the current user (still no silent admin elevation). Auth secrets
    are refused always; with *for_write* Remedy's own installed code is
    refused too (see :func:`refuse_remedy_installed_code_path`).
    """
    scope = normalize_access_scope(access_scope)
    if not roots:
        roots = [Path.cwd()]
    primary = roots[0]
    if not user_path or user_path in (".", "./"):
        out = ensure_project_dir(primary)
        refuse_protected_secret_path(out)
        if for_write:
            refuse_remedy_installed_code_path(out)
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
        if for_write:
            refuse_remedy_installed_code_path(resolved)
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
            _outside_roots_message(user_path, roots, scope, for_write=for_write),
            rule="path_traversal",
            detail={
                "input": user_path,
                "roots": [str(r) for r in roots],
                "scope": scope,
                "for_write": for_write,
            },
        )

    # Relative. Full (warn) may leave the focus folder — still refuse auth.
    if scope == "full":
        try:
            resolved = (primary / candidate).expanduser().resolve()
        except OSError:
            resolved = (primary / candidate).expanduser().absolute()
        refuse_protected_secret_path(resolved)
        if for_write:
            refuse_remedy_installed_code_path(resolved)
        parts_lower = {p.lower() for p in resolved.parts}
        if any(x in parts_lower for x in ("$recycle.bin", "system volume information")):
            raise SecurityError(
                f"Path not allowed: {user_path}",
                rule="path_denied",
                detail={"input": user_path},
            )
        return resolved

    # Relative: try each root; prefer first root that exists
    last_err: Exception | None = None
    for root in roots:
        try:
            out = safe_path(user_path, base_dir=root)
        except Exception as e:
            last_err = e
            continue
        if for_write:
            refuse_remedy_installed_code_path(out)
        return out
    if last_err:
        raise last_err
    out = safe_path(user_path, base_dir=primary)
    if for_write:
        refuse_remedy_installed_code_path(out)
    return out


def _outside_roots_message(
    user_path: str, roots: list[Path], scope: str, *, for_write: bool
) -> str:
    """Denial text that matches the owner's *actual* configuration.

    Never tells the model to "raise access_scope" when the scope already
    covers the machine; under ``home`` it names ``full`` as the next step.
    """
    roots_s = ", ".join(str(r) for r in roots[:4])
    kind = "write" if for_write else "read"
    if scope == "home":
        hint = (
            "access_scope=home covers the project and the user home; this path is "
            "outside both. Only access_scope=full (or Approvals → Full) allows it."
        )
    elif scope == "untrusted":
        hint = "access_scope=untrusted keeps every path inside the project folder."
    else:
        hint = (
            f"access_scope={scope} keeps {kind}s inside the project folder. "
            "Use a path under the focus folder, or ask the owner to raise "
            "access_scope to home/full if edits outside are intended."
        )
    return (
        f"Path outside allowed {kind} roots ({scope}): {user_path}. "
        f"Allowed {kind} roots: [{roots_s}]. {hint}"
    )


def resolve_read_path(
    user_path: str,
    *,
    roots: list[Path],
    access_scope: str,
) -> Path:
    """Reads are never jailed (contract in ``shell_write_jail``).

    Any absolute path the OS user can open is readable at every access scope
    except ``untrusted`` (an explicit sandbox). Relative paths resolve under
    the project root. Auth secrets are refused always.
    """
    scope = normalize_access_scope(access_scope)
    if scope == "untrusted":
        return resolve_under_roots(user_path or ".", roots, access_scope="untrusted")
    return resolve_under_roots(user_path or ".", roots, access_scope="full")


def resolve_write_path(
    user_path: str,
    *,
    roots: list[Path],
    access_scope: str,
    approval_mode: str,
    project_bound: bool,
) -> Path:
    """Writes are jailed to the write roots the owner's scope selects.

    - Approvals → Full, ``access_scope=full``, or no project bound →
      machine-wide (auth secrets + Remedy's installed code still refused).
    - ``home`` → project + home.  ``project`` / ``untrusted`` → project only.
    """
    scope = normalize_access_scope(access_scope)
    approval = (approval_mode or "").strip().lower()
    if approval == "full" or scope == "full" or not project_bound:
        return resolve_under_roots(
            user_path or ".", roots, access_scope="full", for_write=True
        )
    return resolve_under_roots(
        user_path or ".", roots, access_scope=scope, for_write=True
    )


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
    home_dir: Path | str | None = None,
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
            "Relative paths resolve under the focus folder. **Reads are never "
            "jailed** — read/list/search any path on the machine (Downloads, "
            "Documents, other trees) and copy files *into* the project from "
            "anywhere; but **file_write / file_edit and mutating shell commands "
            "stay inside the focus folder only**. Only the owner raises access "
            "scope in Settings (home/full) when multi-tree edits are intended."
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
            "Access scope is **full**: reads and writes across the user machine "
            "(no silent admin elevation). Prefer the focus folder for this "
            "project's edits. Never writable: Remedy's auth secrets and "
            "Remedy's own installed code (frozen sidecar / ~/.remedy/voice/runtime)."
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
        listing = ", ".join(f"{e['name']}/" if e["type"] == "dir" else e["name"] for e in entries)
        lines.append(f"Top-level: {listing}")
    else:
        lines.append("Top-level: (empty or unreadable)")
    with suppress(Exception):
        from remedy.execution.host.stretch import format_home_line

        line = format_home_line(home=home_dir)
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
        return default_home() / "projects" / "New Project"


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

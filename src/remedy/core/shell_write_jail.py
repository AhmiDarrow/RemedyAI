"""Shell write jail — keep ``bash_exec`` mutations inside project write roots.

File tools already use :meth:`resolve_tool_path(for_write=True)`. The shell only
had a *cwd* jail; agents could still ``Set-Content C:\\…\\OtherProject\\…`` and
cross-contaminate sibling trees (SecretFolder vs SecretSticky).

When a project is bound and scope is not owner-``home`` with multi-root writes,
mutation-class shell commands that target paths outside write roots are refused.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Commands / cmdlets that mutate the filesystem (case-insensitive).
# Avoid bare short aliases as bare \bmd\b (matches ".md" extensions).
_MUTATION_HINT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bset-content\b|\bout-file\b|\badd-content\b|\btee-object\b"
    r"|\bnew-item\b|\bcopy-item\b|\bmove-item\b|\bremove-item\b"
    r"|\brename-item\b|\bmkdir\b|\brmdir\b"
    r"|\bwritealltext\b|\bwriteallbytes\b|\bappendalltext\b"
    r"|\bstreamwriter\b|\bfile\.write|\bfile\.create|\bfile\.delete"
    r"|\bfile\.move|\bfile\.copy|\bio\.file\b"
    # Short aliases only as whole shell tokens (not inside .md / names)
    r"|(?:^|[\s;&|(])(?:ni|cp|mv|rm|rd|ren|del|erase|md|tee)(?=[\s]|$)"
    r"|\becho\b(?=[^\n]*>)|\bprintf\b(?=[^\n]*>)|\bcat\b(?=[^\n]*>)"
    r"|\bgit\s+checkout\b|\bgit\s+restore\b|\bgit\s+clean\b|\bgit\s+reset\b"
    r"|\bnpm\s+install\b|\bpip\s+install\b|\bcargo\s+install\b"
    r")"
)

# Redirection that writes a file (not just pipe).
_REDIRECT_WRITE_RE = re.compile(r"(?<![0-9])>{1,2}\s*")

# Absolute Windows / Unix path tokens in a command line.
_ABS_PATH_RE = re.compile(
    r"(?:"
    r'(?:[A-Za-z]:\\|\\\\)[^\s\'"<>|;,&]+'  # C:\… or UNC
    r"|/(?:Users|home|tmp|var|etc|opt|mnt|media)/[^\s'\"<>|;,&]+"  # unix abs common
    r")"
)

# Relative escapes that leave cwd.
_REL_ESCAPE_RE = re.compile(
    r"(?:"
    r'(?:\.\.[/\\])+[^\s\'"<>|;,&]*'  # ../ or ..\
    r"|~[/\\][^\s'\"<>|;,&]*"  # ~/
    r")"
)

# Quoted path groups (prefer these when present).
_QUOTED_PATH_RE = re.compile(
    r"""(?x)
    (?:
        "((?:[A-Za-z]:\\|\\\\|//|\.\.|~/)[^"]+)"
      | '((?:[A-Za-z]:\\|\\\\|//|\.\.|~/)[^']+)'
    )
    """
)


def _norm_roots(roots: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        try:
            out.append(Path(r).expanduser().resolve(strict=False))
        except OSError:
            out.append(Path(r).expanduser().absolute())
    return out


def _under_any(path: Path, roots: list[Path]) -> bool:
    try:
        p = path.expanduser().resolve(strict=False)
    except OSError:
        p = path.expanduser().absolute()
    for root in roots:
        try:
            if p == root or p.is_relative_to(root):
                return True
        except (ValueError, TypeError, OSError):
            try:
                p.relative_to(root)
                return True
            except ValueError:
                continue
    return False


def _clean_token(raw: str) -> str:
    t = (raw or "").strip().strip("`'\"")
    # PowerShell often ends paths with ) ] , ;
    t = t.rstrip(")],;:")
    return t


def extract_path_candidates(command: str) -> list[str]:
    """Extract absolute / escape-relative path strings from a shell command."""
    text = command or ""
    found: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        c = _clean_token(s)
        if len(c) < 2:
            return
        key = c.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(c)

    for m in _QUOTED_PATH_RE.finditer(text):
        _add(m.group(1) or m.group(2) or "")
    for m in _ABS_PATH_RE.finditer(text):
        _add(m.group(0))
    for m in _REL_ESCAPE_RE.finditer(text):
        _add(m.group(0))
    return found


def looks_like_mutation(command: str) -> bool:
    """True when the command is likely to create/modify/delete files."""
    c = command or ""
    if _MUTATION_HINT_RE.search(c):
        return True
    if _REDIRECT_WRITE_RE.search(c):
        return True
    return False


def path_outside_write_roots(
    path_str: str,
    *,
    write_roots: list[Path],
    cwd: Path | None,
) -> Path | None:
    """Return resolved path if it falls outside all write roots, else None."""
    roots = _norm_roots(write_roots)
    if not roots:
        return None
    raw = _clean_token(path_str)
    if not raw:
        return None
    # Bare drive / incomplete
    if re.fullmatch(r"[A-Za-z]:\\?", raw):
        return None

    cand = Path(raw).expanduser()
    if not cand.is_absolute():
        base = cwd or roots[0]
        try:
            base_r = base.expanduser().resolve(strict=False)
        except OSError:
            base_r = base.expanduser().absolute()
        try:
            cand = (base_r / cand).resolve(strict=False)
        except OSError:
            cand = (base_r / cand).absolute()
    else:
        try:
            cand = cand.resolve(strict=False)
        except OSError:
            cand = cand.absolute()

    if _under_any(cand, roots):
        return None
    return cand


def check_shell_write_jail(
    command: str,
    *,
    write_roots: list[Path],
    cwd: Path | None = None,
    project_bound: bool = True,
    access_scope: str = "project",
) -> str | None:
    """Return a block reason if *command* would mutate outside write roots.

    Returns ``None`` when the command is allowed (read-only, no external paths,
    or paths stay inside write roots).
    """
    if not project_bound:
        # No focus folder → owner-machine mode (documented full access).
        return None
    scope = (access_scope or "project").strip().lower()
    # home scope intentionally allows project + home writes.
    # project / untrusted / full-with-bound-project all use project-only write roots.
    if not write_roots:
        return None

    cmd = command or ""
    if not cmd.strip():
        return None

    if not looks_like_mutation(cmd):
        return None

    offenders: list[str] = []
    for token in extract_path_candidates(cmd):
        outside = path_outside_write_roots(
            token, write_roots=write_roots, cwd=cwd
        )
        if outside is not None:
            offenders.append(str(outside))

    if not offenders:
        return None

    roots_s = ", ".join(str(r) for r in _norm_roots(write_roots)[:4])
    bad = offenders[0]
    return (
        f"shell write jail: mutation targets path outside project write roots: {bad}. "
        f"Allowed write roots: [{roots_s}]. "
        "Use file_write/file_edit under the focus folder, or raise access_scope "
        "to home only if multi-tree edits are intended. Do not write sibling "
        "projects (e.g. SecretFolder vs SecretSticky) from this session."
    )

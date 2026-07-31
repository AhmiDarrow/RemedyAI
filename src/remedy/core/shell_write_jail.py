"""Shell write jail — keep ``bash_exec`` mutations inside project write roots.

File tools already use :meth:`resolve_tool_path(for_write=True)`. The shell only
had a *cwd* jail; agents could still ``Set-Content C:\\…\\OtherProject\\…`` and
cross-contaminate sibling trees (SecretFolder vs SecretSticky).

When a project is bound, mutation-class shell commands that target paths outside
write roots — or that hide the target path (env expansion / Join-Path / python
-c) while still looking like a write — are refused.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

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
    r"|\b\[(?:system\.)?io\.file\]::"
    r"|\b(?:xcopy|robocopy)\b"
    # Short aliases as whole tokens (not inside .md): include sc (Set-Content)
    r"|(?:^|[\s;&|(])(?:sc|ni|cp|copy|mv|rm|rd|ren|del|erase|md|tee|xcopy|robocopy)"
    r"(?=[\s]|$)"
    r"|\becho\b(?=[^\n]*>)|\bprintf\b(?=[^\n]*>)|\bcat\b(?=[^\n]*>)"
    r"|\bgit\s+checkout\b|\bgit\s+restore\b|\bgit\s+clean\b|\bgit\s+reset\b"
    r"|\bnpm\s+install\b|\bpip\s+install\b|\bcargo\s+install\b"
    # Download / fetch to file
    r"|\binvoke-webrequest\b|\biwr\b"
    r"|\bcurl\b|\bwget\b"
    # Interpreter one-shot writes
    r"|\b(?:python|python3|py)\s+(?:-\w+\s+)*-c\b"
    r"|\bnode\s+(?:-\w+\s+)*-e\b"
    # Windows FS utilities that create/mutate without cmdlets above
    r"|\bfsutil\b|\bmklink\b"
    r")"
)

# Redirection that writes a file (not just pipe).
_REDIRECT_WRITE_RE = re.compile(r"(?<![0-9])>{1,2}\s*")

# Path-like tokens that cannot be proven under write roots (obfuscation).
_OPAQUE_PATH_HINT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\$env:[A-Za-z_][\w]*"
    r"|\$\{env:[A-Za-z_][\w]*\}"
    # cmd.exe / delayed-expansion env vars used as path roots
    r"|%[A-Za-z_][\w]*%"
    r"|![A-Za-z_][\w]*!"
    r"|\bjoin-path\b"
    r"|\benviron\["
    r"|\bos\.environ"
    r"|\bprocess\.env\b"
    r"|\bos\.homedir\b"
    r"|\bexpanduser\b"
    r"|\bpath\.home\b"
    r"|\bgetfolderpath\b"
    r"|\binvoke-webrequest\b.*-outfile\b"
    r"|\biwr\b[^\n]*-outfile\b"
    r"|\bcurl\b[^\n]*\s-o\b"
    r"|\bwget\b[^\n]*\s-O\b"
    r")"
)

# Interpreter one-shots: without proven in-root path tokens, cannot prove safety.
_INTERPRETER_ONESHOT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\b(?:python|python3|py)\s+(?:-\w+\s+)*-c\b"
    r"|\bnode\s+(?:-\w+\s+)*-e\b"
    r")"
)

# Encoded / archive / decode / download writers — path often hidden; fail closed when project-bound.
_OPAQUE_MUTATION_RE = re.compile(
    r"(?ix)"
    r"(?:"
    # -EncodedCommand / -enc / -ec (no \b before '-' — space and '-' are both non-word)
    r"(?:^|[\s;|&])-(?:encodedcommand|enc|ec)\b"
    # Short -e after powershell/pwsh (classic bypass; bare -e alone is too noisy)
    r"|(?:powershell|pwsh)(?:\.exe)?(?:\s+[/\-\w]+)*\s+-e(?:\s|$|=)"
    r"|\bexpand-archive\b|\bcompress-archive\b"
    r"|\btar\s+-[a-z]*x|\btar\s+--extract\b"
    r"|\bcertutil\b[^\n]*-(?:decode|urlcache)\b"
    r"|\bbitsadmin\b|\bstart-bitstransfer\b"
    r"|\binvoke-webrequest\b[^\n]*-outfile\b"
    r"|\binvoke-restmethod\b[^\n]*-outfile\b"
    r"|\binvoke-expression\b|\biex\b"
    r"|\bstart-process\b[^\n]*-argumentlist\b"
    r"|\badd-type\b[^\n]*-typedefinition\b"
    # Global package installs write outside project write roots (red-team 2026-07-30)
    r"|\bnpm\s+(?:install|i|add)\b[^\n]*?(?:^|[\s])(?:-g|--global)(?:[\s]|$)"
    r"|\bnpm\s+(?:-g|--global)\s+(?:install|i|add)\b"
    r"|\byarn\s+global\s+add\b"
    r"|\bpnpm\s+add\s+-g\b|\bpnpm\s+add\s+--global\b"
    r"|\bcargo\s+install\b"
    r"|\bgem\s+install\b"
    r"|\bgo\s+install\b"
    r"|\bpip(?:3)?\s+install\b[^\n]*\s--user\b"
    r"|\bpython(?:3)?\s+-m\s+pip\s+install\b[^\n]*\s--user\b"
    # .NET download / write helpers that hide destinations
    r"|\b(?:system\.)?net\.webclient\b|\bnew-object\b[^\n]*webclient\b"
    r"|\bdownloadfile\b|\bdownloadstring\b|\bdownloaddata\b"
    r"|\bfrombase64string\b"
    r")"
)

# Bare PowerShell variables used as path targets (not $env: which is covered above)
_BARE_PS_VAR_PATH_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:-path|-literalpath|-destination|-destinationpath|-outfile|-file)\s+"
    r"\$[A-Za-z_][\w]*"
    r"|\b(?:set-content|out-file|add-content|copy-item|move-item|remove-item|"
    r"new-item|rename-item)\b[^\n]*\$[A-Za-z_][\w]*"
    r")"
)

# Absolute Windows / Unix path tokens in a command line.
_ABS_PATH_RE = re.compile(
    r"(?:"
    r'(?:[A-Za-z]:\\|\\\\)[^\s\'"<>|;,&]+'  # C:\… or UNC
    r"|/(?:Users|home|tmp|var|etc|opt|mnt|media)/[^\s'\"<>|;,&]+"
    r")"
)

# Relative escapes that leave cwd.
_REL_ESCAPE_RE = re.compile(
    r"(?:"
    r'(?:\.\.[/\\])+[^\s\'"<>|;,&]*'
    r"|~[/\\][^\s'\"<>|;,&]*"
    r")"
)

_QUOTED_PATH_RE = re.compile(
    r"""(?x)
    (?:
        "((?:[A-Za-z]:\\|\\\\|//|\.\.|~/|\$)[^"]+)"
      | '((?:[A-Za-z]:\\|\\\\|//|\.\.|~/|\$)[^']+)'
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
    return bool(_REDIRECT_WRITE_RE.search(c))


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
    if re.fullmatch(r"[A-Za-z]:\\?", raw):
        return None
    # Unexpanded variables — cannot prove under roots
    if "$" in raw or "%" in raw:
        return Path(raw)

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

    # Auth secrets are never shell-writable — even when home/full write roots
    # contain ~/.remedy/auth (or $REMEDY_HOME/auth) as a subpath.
    try:
        from remedy.core.security import is_protected_secret_path

        if is_protected_secret_path(cand):
            return cand
    except Exception:
        pass

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

    Returns ``None`` when allowed. ``access_scope`` is reserved for callers that
    already folded scope into *write_roots* (home expands roots to project+home).
    """
    _ = access_scope  # roots are authoritative; keep param for API stability
    if not project_bound:
        return None
    if not write_roots:
        return (
            "shell write jail: no write roots available while project is bound "
            "(fail closed). Retry after project path is set."
        )

    cmd = command or ""
    if not cmd.strip():
        return None

    # Normalize roots once per check (hot path — bash_exec every mutation).
    roots = _norm_roots(write_roots)
    roots_s = ", ".join(str(r) for r in roots[:4])

    # Encoded / archive / decode / download writers hide destinations — fail closed.
    if _OPAQUE_MUTATION_RE.search(cmd):
        return (
            "shell write jail: encoded/archive/decode mutation cannot be proven "
            f"under write roots [{roots_s}]. Prefer file_write/file_edit with "
            "paths under the focus folder (or Expand-Archive with explicit "
            "DestinationPath under the project)."
        )

    if not looks_like_mutation(cmd):
        return None

    # Set-Content -Path $dest  (bare PS var) — cannot prove destination
    if _BARE_PS_VAR_PATH_RE.search(cmd):
        return (
            "shell write jail: mutation uses a PowerShell variable as the path "
            f"(cannot prove under write roots [{roots_s}]). Use a literal path "
            "under the focus folder with file_write/file_edit."
        )

    candidates = extract_path_candidates(cmd)
    offenders: list[str] = []
    for token in candidates:
        outside = path_outside_write_roots(
            token, write_roots=roots, cwd=cwd
        )
        if outside is not None:
            offenders.append(str(outside))

    # Mutation + opaque path construction → deny even if another candidate is
    # in-root (e.g. `copy C:\proj\a $env:USERPROFILE\Desktop\b`). Opaque dests
    # are not extractable as path tokens, so mixed forms must fail closed.
    if not offenders and _OPAQUE_PATH_HINT_RE.search(cmd):
        return (
            "shell write jail: mutation uses opaque path construction "
            "($env:/%VAR%/Join-Path/process.env/curl -o/etc.) that cannot be "
            "proven under write roots. "
            f"Allowed write roots: [{roots_s}]. Prefer file_write/file_edit with "
            "paths under the focus folder."
        )

    # python -c / node -e: without extractable path tokens we cannot prove the
    # write target (open/writeFile/pathlib/dynamic paths). Fail closed.
    # When every candidate is under roots (no offenders), allow proven in-root
    # one-shots such as python -c "open(r'<project>\\x','w')…".
    if not offenders and not candidates and _INTERPRETER_ONESHOT_RE.search(cmd):
        return (
            "shell write jail: interpreter -c/-e write cannot be proven under "
            f"write roots [{roots_s}]. Use file_write/file_edit, or pass a "
            "literal path under the focus folder."
        )

    # Mutation with zero extractable path tokens: allow only if cwd is under
    # write roots (npm install / git write inside project). Else fail closed.
    if not offenders and not candidates:
        if cwd is not None:
            try:
                from pathlib import Path as _P

                c = _P(cwd).expanduser().resolve()
                if _under_any(c, roots):
                    return None
            except Exception:
                pass
        return (
            "shell write jail: mutation command has no proven path under write "
            f"roots [{roots_s}] and cwd is not inside a write root. "
            "Prefer file_write/file_edit, or run with project workdir set."
        )

    if not offenders:
        return None

    bad = offenders[0]
    try:
        from remedy.core.security import is_protected_secret_path

        if any(is_protected_secret_path(o) for o in offenders):
            return (
                "shell write jail: mutation targets protected Remedy auth secrets "
                f"({bad}). Never read/write ~/.remedy/auth or $REMEDY_HOME/auth "
                "from the shell — keys and tokens stay out of tool paths."
            )
    except Exception:
        pass
    return (
        f"shell write jail: mutation targets path outside project write roots: {bad}. "
        f"Allowed write roots: [{roots_s}]. "
        "Use file_write/file_edit under the focus folder, or raise access_scope "
        "to home only if multi-tree edits are intended. Do not write sibling "
        "projects (e.g. SecretFolder vs SecretSticky) from this session."
    )

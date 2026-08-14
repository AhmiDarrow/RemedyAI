"""Shell write jail — keep ``bash_exec`` mutations inside project write roots.

File tools already use :meth:`resolve_tool_path(for_write=True)`. The shell only
had a *cwd* jail; agents could still ``Set-Content C:\\…\\OtherProject\\…`` and
cross-contaminate sibling trees (SecretFolder vs SecretSticky).

When a project is bound, mutation-class shell commands that target paths outside
write roots — or that hide the target path (env expansion / Join-Path / python
-c) while still looking like a write — are refused.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from contextlib import suppress
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
    # cmd `move` / PS `ac` `ri` `mi` `cpi` `si` are real write verbs.
    r"|(?:^|[\s;&|(])(?:sc|ni|cp|copy|move|mv|rm|rd|ren|del|erase|md|tee|xcopy|robocopy|ac|ri|mi|cpi|si)"
    r"(?=[\s]|$)"
    r"|\becho\b(?=[^\n]*>)|\bprintf\b(?=[^\n]*>)|\bcat\b(?=[^\n]*>)"
    r"|\bgit\s+checkout\b|\bgit\s+restore\b|\bgit\s+clean\b|\bgit\s+reset\b"
    r"|\bnpm\s+install\b|\bpip\s+install\b|\bcargo\s+install\b"
    # Download / fetch to file
    r"|\binvoke-webrequest\b|\biwr\b"
    r"|\bcurl\b|\bwget\b"
    # Interpreter one-shot writes (python.exe / node.exe are the Windows default)
    r"|\b(?:pythonw|python3|python|py|nodejs|node)(?:\.exe)?\s+(?:-\w+\s+)*-c\b"
    r"|\b(?:nodejs|node)(?:\.exe)?\s+(?:-\w+\s+)*-e\b"
    # Windows FS utilities that create/mutate without cmdlets above
    r"|\bfsutil\b|\bmklink\b"
    r")"
)

# Redirection that writes a file. Include numbered forms (`1>` / `2>`) so
# `dir 1> C:\Users\Public\pwn.txt` is a mutation. `2>&1` is a dup, not a dest.
_REDIRECT_WRITE_RE = re.compile(r"(?<!&)\d*>{1,2}(?!&)\s*")
_REDIRECT_DEVNULL_RE = re.compile(
    r"(?ix)(?:\d*>{1,2})\s*(?:nul|null|/dev/null|con)\b"
)

# Path-like tokens that cannot be proven under write roots (obfuscation).
_OPAQUE_PATH_HINT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\$env:[A-Za-z_][\w]*"
    r"|\$\{env:[A-Za-z_][\w]*\}"
    # PowerShell automatic variables (not only $env:USERPROFILE)
    r"|\$\{?(?:HOME|USERPROFILE|HOMEPATH)\}?\b"
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
    r"|\bos\.path\b"
    r"|\bos\.sep\b"
    r"|Path\s*\(\s*['\"][A-Za-z]:[\\/]"
    r"|Path\s*\(\s*['\"]/"
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
    r"\b(?:pythonw|python3|python|py)(?:\.exe)?\s+(?:-\w+\s+)*-c\b"
    r"|\b(?:nodejs|node)(?:\.exe)?\s+(?:-\w+\s+)*-e\b"
    r"|\b(?:powershell|pwsh)(?:\.exe)?[^\n]*-(?:command|c)\b"
    r")"
)

# Read-only probes (print / console.log) — allowed under a project cwd.
_READONLY_ONESHOT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\b(?:pythonw|python3|python|py)(?:\.exe)?\s+(?:-\w+\s+)*-c\s+"
    r"""['\"]\s*(?:print|help)\s*\([^'\"]*\)\s*['\"]"""
    r"|"
    r"\b(?:node|nodejs)(?:\.exe)?\s+(?:-\w+\s+)*-e\s+"
    r"""['\"]\s*console\.log\s*\([^'\"]*\)\s*['\"]"""
    r")"
)

# Python dest construction that hides an absolute write (host_script / -c).
_PY_CONSTRUCTED_DEST_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bos\.path\b"
    r"|\bos\.sep\b"
    r"|\bos\.environ\b"
    r"|Path\s*\(\s*['\"][A-Za-z]:[\\/]"
    r"|Path\s*\(\s*['\"]/"
    r"|\bchr\s*\("
    r")"
)

# Nested open(os.path.join(…), 'w') — do not require a same-depth 'w'.
_PY_WRITE_API_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\.write_text\b|\.write_bytes\b|\.write\b"
    r"|open\s*\("
    r"|\.touch\b|\.mkdir\b|\.unlink\b|\.rmdir\b"
    r"|os\.remove\b|os\.unlink\b"
    r"|shutil\.(?:copy|move|rmtree)"
    r"|writefilesync\b"
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
    r"|\bdotnet\s+tool\s+install\b[^\n]*?(?:^|[\s])(?:-g|--global)(?:[\s]|$)"
    r"|\bdotnet\s+tool\s+install\s+--global\b"
    # Privilege / machine-wide mutation (opaque — not a project file write)
    r"|\bschtasks\b[^\n]*/create\b"
    r"|\bnet\s+user\b"
    r"|\breg\s+add\b|\breg\s+delete\b|\breg\s+import\b"
    r"|\btakeown\b|\bicacls\b"
    # Nested shells carrying privilege / write payloads (fail closed)
    r"|\b(?:bash|sh|zsh)\s+(?:-c|-lc)\b[^\n]*"
    r"(?:reg\s+add|net\s+user|schtasks|/create|set-content|out-file|>)"
    r"|\b(?:powershell|pwsh)(?:\.exe)?[^\n]*-(?:command|c)\b[^\n]*"
    r"(?:reg\s+add|net\s+user|schtasks|set-content|out-file)"
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

# Interpreter / compiler / runtime binaries are *invoked*, not written.
# Models often emit ``C:\Python312\python.exe game.py`` — the .exe is not a
# write destination and must not trip the project write jail.
_RUNTIME_BIN_NAMES = frozenset(
    {
        "python",
        "python3",
        "pythonw",
        "py",
        "pip",
        "pip3",
        "node",
        "nodejs",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "gcc",
        "g++",
        "c++",
        "clang",
        "clang++",
        "cl",
        "rustc",
        "cargo",
        "go",
        "javac",
        "java",
        "cmake",
        "ninja",
        "make",
        "nmake",
        "msbuild",
        "dotnet",
        "pytest",
        "uv",
        "ruff",
        "mypy",
        "git",
        "gh",
        "pwsh",
        "powershell",
        "cmd",
        "bash",
        "sh",
    }
)


_FIRST_TOKEN_RE = re.compile(r"""^\s*(?:"([^"]*)"|'([^']*)'|(\S+))""")


def _split_statements(command: str) -> list[str]:
    """Split on | & ; / newlines, ignoring separators inside quotes."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    s = command or ""
    while i < len(s):
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in "|&;\n":
            part = "".join(buf)
            if part.strip():
                out.append(part)
            buf = []
            while i < len(s) and s[i] in "|&;\n":
                i += 1
            continue
        buf.append(ch)
        i += 1
    part = "".join(buf)
    if part.strip():
        out.append(part)
    return out


def _rest_after_first_token(part: str) -> str:
    m = _FIRST_TOKEN_RE.match(part or "")
    if not m:
        return part or ""
    return (part or "")[m.end() :]


def is_runtime_executable_path(path_str: str) -> bool:
    """True when *path_str* is a known interpreter/compiler binary, not a write dest."""
    raw = _clean_token(path_str)
    if not raw:
        return False
    name = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    lower = name.lower()
    # python_pwned.txt / notes.md are write destinations, not runtimes.
    if "." in lower:
        ext = lower.rsplit(".", 1)[-1]
        if ext not in {"exe", "cmd", "bat", "com", "bin"}:
            return False
    # strip version suffixes: python3.12.exe, gcc-14
    base = lower[:-4] if lower.endswith(".exe") else lower
    stem = base.split(".", 1)[0]  # python3.12 → python3
    if base in _RUNTIME_BIN_NAMES or stem in _RUNTIME_BIN_NAMES:
        return True
    # python3.12 / python312 — whole token, not startswith (python_notes.txt)
    compact = stem.replace(".", "")
    return bool(re.fullmatch(r"python\d*", stem) or re.fullmatch(r"python\d*", compact))


# Absolute Windows / Unix path tokens in a command line.
# Drive-letter must accept C:/ as well as C:\ — POSIX slashes are absolute on Windows.
_ABS_PATH_RE = re.compile(
    r"(?:"
    r'(?:[A-Za-z]:[\\/]|\\\\)[^\s\'"<>|;,&]+'  # C:\… C:/… or UNC
    r"|/(?:Users|home|tmp|var|etc|opt|mnt|media)/[^\s'\"<>|;,&]+"
    # Windows root-relative: \Users\Public\x  (cwd-join becomes C:\Users\…)
    r"|\\(?:Users|Windows|Program Files|ProgramData|System32)[^\s'\"<>|;,&]*"
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
        "((?:[A-Za-z]:[\\/]|\\\\|//|\.\.|~/|\$)[^"]+)"
      | '((?:[A-Za-z]:[\\/]|\\\\|//|\.\.|~/|\$)[^']+)'
    )
    """
)

# `> $HOME\…` / `>> "$USERPROFILE\…"` — dest is an unproven variable.
_REDIRECT_DOLLAR_DEST_RE = re.compile(r"(?<!&)\d*>{1,2}(?!&)\s*['\"]?\$")


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


# Interpreter / script launches can write anywhere once running — treat as
# opaque mutations when project-bound (S-WS-01 residual).
_SCRIPT_LAUNCH_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\b(?:pythonw|python3|python|py)(?:\.exe)?\s+(?!-c\b)[^\n|&;]+"
    r"|\b(?:nodejs|node)(?:\.exe)?\s+(?!-e\b)[^\n|&;]+"
    r"|\b(?:ruby|perl|php|lua|bash|sh|zsh|pwsh|powershell)(?:\.exe)?\s+"
    r"(?:-[Ff]ile\s+|[^\n|&;-][^\n|&;]*)"
    r"|\b(?:cmd)(?:\.exe)?\s+/[Cc]\s+"
    r"|(?:^|[\s;&|(])(?:\.\\|\./|/|[A-Za-z]:[\\/])[^\s|&;]+\.(?:py|js|mjs|cjs|ps1|bat|cmd|exe|vbs)\b"
    r")"
)


def looks_like_mutation(command: str) -> bool:
    """True when the command is likely to create/modify/delete files."""
    c = command or ""
    if _MUTATION_HINT_RE.search(c):
        return True
    if _SCRIPT_LAUNCH_RE.search(c):
        return True
    if _REDIRECT_WRITE_RE.search(c):
        # `2>nul` / `2>/dev/null` alone is not a write dest we care about
        stripped = _REDIRECT_DEVNULL_RE.sub("", c)
        return bool(_REDIRECT_WRITE_RE.search(stripped))
    return False


def _is_windows_abs_path(raw: str) -> bool:
    """Drive-letter or UNC path — absolute on Windows even if POSIX Path disagrees."""
    s = raw or ""
    if re.match(r"^[A-Za-z]:[\\/]", s):
        return True
    return s.startswith("\\\\") or s.startswith("//")


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

    # Windows absolute paths must not resolve under POSIX cwd as relative tokens
    # (Linux CI: Path(r"C:\Users\…") is relative → wrongly appears inside project).
    if _is_windows_abs_path(raw):
        if os.name != "nt":
            return Path(raw)
        try:
            cand = Path(raw).expanduser().resolve(strict=False)
        except OSError:
            cand = Path(raw).expanduser().absolute()
        try:
            from remedy.core.security import is_protected_secret_path

            if is_protected_secret_path(cand):
                return cand
        except Exception:
            pass
        if _under_any(cand, roots):
            return None
        return cand

    # Normalize Windows-style relative escapes on non-Windows so ..\sibling works.
    norm = raw.replace("\\", "/") if os.name != "nt" and "\\" in raw else raw
    cand = Path(norm).expanduser()
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


# Home/profile construction in launched script text.
_SCRIPT_HOME_PATH_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bpath\.home\b"
    r"|\bexpanduser\b"
    r"|\$env:(?:USERPROFILE|HOME|HOMEPATH)\b"
    r"|\$\{env:(?:USERPROFILE|HOME|HOMEPATH)\}"
    r"|\$\{?(?:HOME|USERPROFILE|HOMEPATH)\}?\b"
    r"|%(?:USERPROFILE|HOME|HOMEPATH)%"
    r")"
)


def _roots_cover_user_profile(roots: list[Path]) -> bool:
    homes: list[Path] = []
    with suppress(OSError):
        homes.append(Path.home())
    for key in ("USERPROFILE", "HOME"):
        raw = os.environ.get(key)
        if raw:
            homes.append(Path(raw))
    return any(_under_any(h, roots) for h in homes)


_AUTH_HINT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\.remedy[/\\]+auth\b"
    r"|[/\\]auth[/\\](?:local_api_token|provider_keys|oauth)[^\s'\"<>|;,&]*"
    r")"
)


def check_shell_secret_access(command: str) -> str | None:
    """Block read or write of ``~/.remedy/auth`` even for ``type`` / Get-Content."""
    cmd = command or ""
    if not cmd.strip():
        return None
    if _AUTH_HINT_RE.search(cmd):
        return (
            "shell jail: refuse access to Remedy auth secrets "
            "(~/.remedy/auth). Keys and tokens stay out of the shell."
        )
    for token in extract_path_candidates(cmd):
        try:
            from remedy.core.security import is_protected_secret_path

            if is_protected_secret_path(token):
                return (
                    "shell jail: refuse access to Remedy auth secrets "
                    f"({token}). Keys and tokens stay out of the shell."
                )
        except Exception:
            continue
    return None


def scan_script_source_for_outside_writes(
    script: Path,
    *,
    write_roots: list[Path],
    project_bound: bool = True,
) -> str | None:
    """Fail closed when a launched script references auth, home/profile, or outside paths."""
    try:
        text = script.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return None
    if _AUTH_HINT_RE.search(text):
        return (
            "shell write jail: script references Remedy auth secrets "
            f"({script.name}). Refuse to run."
        )
    if not project_bound:
        return None
    roots = _norm_roots(write_roots)
    code = "\n".join(
        "" if line.lstrip().startswith("#") else line for line in text.splitlines()
    )
    if _SCRIPT_HOME_PATH_RE.search(code) and not _roots_cover_user_profile(roots):
        return (
            "shell write jail: script uses home/profile path construction "
            f"(Path.home/expanduser/$env:USERPROFILE/%USERPROFILE%) ({script.name}). "
            "Refuse to run."
        )
    # Constructed dests (Path('C:/')/'Users'/…, os.path/os.sep) hide writes.
    if _PY_WRITE_API_RE.search(code) and _PY_CONSTRUCTED_DEST_RE.search(code):
        return (
            "shell write jail: script uses constructed path I/O "
            f"(Path(drive)/os.path/os.sep) ({script.name}). "
            "Refuse to run. Prefer file_write under the focus folder."
        )
    for m in _ABS_PATH_RE.finditer(text):
        tok = m.group(0)
        try:
            from remedy.core.security import is_protected_secret_path

            if is_protected_secret_path(tok):
                return (
                    "shell write jail: script references Remedy auth secrets "
                    f"({script.name}). Refuse to run."
                )
        except Exception:
            pass
        off = path_outside_write_roots(tok, write_roots=roots, cwd=script.parent)
        if off is not None:
            return (
                f"shell write jail: script {script.name} references path "
                f"outside write roots: {off}"
            )
    return None


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
    cmd = command or ""
    secret_hit = check_shell_secret_access(cmd)
    if secret_hit:
        return secret_hit
    if not project_bound:
        return None
    if not write_roots:
        return (
            "shell write jail: no write roots available while project is bound "
            "(fail closed). Retry after project path is set."
        )

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

    def _cwd_in_roots() -> bool:
        if cwd is None:
            return False
        try:
            from pathlib import Path as _P

            return _under_any(_P(cwd).expanduser().resolve(), roots)
        except Exception:
            return False

    readonly_oneshot = bool(_READONLY_ONESHOT_RE.search(cmd) and _cwd_in_roots())

    # `echo pwn > $HOME\…` / `'pwn' > "$USERPROFILE\…"` — dest is opaque.
    if _REDIRECT_DOLLAR_DEST_RE.search(cmd):
        return (
            "shell write jail: mutation redirects to a $-variable destination "
            f"(cannot prove under write roots [{roots_s}]). Use a literal path "
            "under the focus folder with file_write/file_edit."
        )

    # Set-Content -Path $dest  (bare PS var) — cannot prove destination
    if _BARE_PS_VAR_PATH_RE.search(cmd):
        return (
            "shell write jail: mutation uses a PowerShell variable as the path "
            f"(cannot prove under write roots [{roots_s}]). Use a literal path "
            "under the focus folder with file_write/file_edit."
        )

    candidates = extract_path_candidates(cmd)
    offenders: list[str] = []
    # Dest checks use each statement *after* argv[0], so invoking
    # python.exe is not a write, but copy … python.exe still is.
    for part in _split_statements(cmd):
        rest = _rest_after_first_token(part)
        if not rest.strip():
            continue
        for token in extract_path_candidates(rest):
            outside = path_outside_write_roots(
                token, write_roots=roots, cwd=cwd
            )
            if outside is not None:
                offenders.append(str(outside))

    # Mutation + opaque path construction → deny even if another candidate is
    # in-root (e.g. `copy C:\proj\a $env:USERPROFILE\Desktop\b`). Opaque dests
    # are not extractable as path tokens, so mixed forms must fail closed.
    if not offenders and _OPAQUE_PATH_HINT_RE.search(cmd) and not readonly_oneshot:
        return (
            "shell write jail: mutation uses opaque path construction "
            "($env:/%VAR%/Join-Path/process.env/curl -o/etc.) that cannot be "
            "proven under write roots. "
            f"Allowed write roots: [{roots_s}]. Prefer file_write/file_edit with "
            "paths under the focus folder."
        )

    # Oneshot: fail closed when payload remains after subtracting in-root dests
    # (in-root decoy + Path('C:/')/'Users'/… must not slip).
    if not offenders and _INTERPRETER_ONESHOT_RE.search(cmd) and not readonly_oneshot:
        remaining = cmd
        for tok in candidates:
            if path_outside_write_roots(tok, write_roots=roots, cwd=cwd) is None:
                remaining = remaining.replace(tok, "")
        leftover = bool(
            extract_path_candidates(remaining)
            or _OPAQUE_PATH_HINT_RE.search(remaining)
            or _PY_CONSTRUCTED_DEST_RE.search(remaining)
        )
        if leftover or not candidates:
            return (
                "shell write jail: interpreter -c/-e cannot be proven under "
                f"write roots [{roots_s}]. Prefer file_write/file_edit, run with "
                "project workdir=, or pass a literal path under the focus folder."
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

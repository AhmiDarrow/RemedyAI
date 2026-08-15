"""Deterministic POSIX → Windows-cmd rewrite for model-emitted shell strings.

Not a bash. Known-safe substitutions plus "leave it / diagnose" for the rest.
PowerShell payloads are *not* rewritten here — the runner sends them through
a temp ``.ps1`` and ``pwsh -File``.
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import suppress
from dataclasses import dataclass, field

# Operators we split on at top level (longest first).
_CHAIN_OPS = ("&&", "||", ">>", "2>&1", "2>", "1>", "&>", ">&", "|", ";", "&")

_UNTRANSLATABLE = re.compile(
    r"(?<!\$)\$\([^)]+\)|`[^`]+`|\$\{[^}]+\}",
)

# Strong PowerShell-only signals. Do not use POSIX `test -eq` or `start-server`.
# powershell/pwsh are *not* listed here — a later-segment mention ("use powershell")
# must not skip POSIX rewrite. Head match is _PS_HEAD.
_PS_STRONG = re.compile(
    r"(?is)("
    r"\$_\b"
    r"|\$PSVersionTable"
    r"|\$env:[A-Za-z]"
    r"|\bparam\s*\("
    r"|@['\"]"
    r")"
)
# Same idea as scriptfile._PS_WRAPPER: optional path prefix, command head only.
_PS_HEAD = re.compile(
    r"(?is)^\s*(?:(?:[A-Za-z]:\\)?(?:[^\s\"']*[\\/])?)?(?:powershell|pwsh)(?:\.exe)?\b"
)
_PS_CMDLET = re.compile(
    r"(?i)\b(?:Get|Set|New|Remove|Invoke|Write|Select|Where|ForEach|Out|"
    r"Add|Clear|ConvertTo|ConvertFrom|Import|Export|Start|Stop|Test|Measure)"
    r"-([A-Za-z][A-Za-z0-9]+)\b"
)
# Nouns that collide with POSIX / script names (start-server, start-dev).
# "service" is a real PS noun (Get-Service / Start-Service) — do not denylist it.
_PS_FILENAME_NOUNS = frozenset(
    {
        "server",
        "dev",
        "app",
        "all",
        "here",
        "now",
        "script",
        "build",
        "web",
        "api",
    }
)


@dataclass
class TranslateResult:
    text: str
    changed: bool = False
    notes: list[str] = field(default_factory=list)
    untranslatable: bool = False
    noop: bool = False


def looks_like_powershell(command: str) -> bool:
    """True when the string is PowerShell, not POSIX/cmd or a script name."""
    cmd = (command or "").strip()
    if not cmd:
        return False
    if _PS_HEAD.match(cmd):
        return True
    if _PS_STRONG.search(cmd):
        return True
    for m in _PS_CMDLET.finditer(cmd):
        if m.group(1).lower() in _PS_FILENAME_NOUNS:
            continue
        end = m.end()
        trail = cmd[end : end + 8]
        if re.match(r"\.(sh|bash|zsh|py|js|ts|exe|bat|cmd)\b", trail, re.I):
            continue
        return True
    return False


def translate_posix_to_host(
    command: str,
    *,
    host: str | None = None,
) -> TranslateResult:
    """Rewrite POSIX-ish *command* for the host shell.

    *host* defaults to ``cmd`` on Windows and ``posix`` elsewhere. Tests pass
    ``host="cmd"`` to exercise the rewrite table on any OS.
    """
    raw = (command or "").strip()
    if not raw:
        return TranslateResult(text=raw)
    resolved = host
    if resolved is None:
        resolved = "cmd" if os.name == "nt" else "posix"
    if resolved != "cmd":
        return TranslateResult(text=raw)
    if looks_like_powershell(raw):
        return TranslateResult(text=raw, notes=["powershell payload — not posix-rewritten"])

    notes: list[str] = []
    # Whole-string substitutions that are safe even inside chains
    rewritten = _rewrite_redirections(raw)
    if rewritten != raw:
        notes.append("redirect /dev/null → NUL")

    if _UNTRANSLATABLE.search(rewritten):
        return TranslateResult(
            text=rewritten,
            changed=rewritten != raw,
            notes=notes + ["untranslatable substitution $(…) / backticks — use host_script"],
            untranslatable=True,
        )

    parts = _split_top_level(rewritten)
    kept: list[tuple[str, str]] = []
    changed = rewritten != raw
    for seg, op in parts:
        new_seg, seg_notes = _rewrite_segment(seg)
        notes.extend(seg_notes)
        dropped_chmod = (not new_seg) and any(
            n.startswith("chmod ignored") for n in seg_notes
        )
        if dropped_chmod:
            changed = True
            continue
        if new_seg != seg:
            changed = True
        kept.append((new_seg, op))
    if not kept:
        if any(n.startswith("chmod ignored") for n in notes):
            return TranslateResult(text=raw, changed=True, notes=notes, noop=True)
        return TranslateResult(text="", changed=changed, notes=notes)
    out_segs: list[str] = []
    for i, (seg, op) in enumerate(kept):
        out_segs.append(seg)
        if op and i < len(kept) - 1:
            if op == ";":
                out_segs.append(" & ")
            else:
                out_segs.append(f" {op} " if op in ("&&", "||", "|", "&") else f" {op} ")
    text = "".join(out_segs).strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return TranslateResult(text=text, changed=changed, notes=notes)


def _rewrite_redirections(text: str) -> str:
    s = text
    s = re.sub(r"&>\s*/dev/null", ">NUL 2>&1", s)
    s = re.sub(r"2>\s*/dev/null", "2>NUL", s)
    s = re.sub(r">\s*/dev/null", ">NUL", s)
    s = s.replace("/dev/null", "NUL")
    return s


def _split_top_level(command: str) -> list[tuple[str, str]]:
    """Split on top-level chain operators; return (segment, op_after)."""
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    i = 0
    n = len(command)
    quote = ""
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote and command[i - 1 : i] != "\\":
                quote = ""
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        matched = ""
        for op in _CHAIN_OPS:
            if command.startswith(op, i):
                # Don't treat a lone & in 2>&1 as a background op — already matched
                matched = op
                break
        if matched:
            # 2>&1 / 2> / 1> / >> stay attached to the segment (redirection)
            if matched in ("2>&1", "2>", "1>", ">>", "&>", ">&"):
                buf.append(matched)
                i += len(matched)
                continue
            parts.append(("".join(buf).strip(), matched))
            buf = []
            i += len(matched)
            continue
        buf.append(ch)
        i += 1
    parts.append(("".join(buf).strip(), ""))
    return [(s, op) for s, op in parts if s or op]


def _tokens(segment: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r'"[^"]*"|\'[^\']*\'|\S+', segment.strip())]


def _unquote(tok: str) -> str:
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
        return t[1:-1]
    return t


def _q(path: str) -> str:
    p = path.replace("/", "\\") if ("/" in path or os.name == "nt") else path
    if not p:
        return '""'
    if len(p) >= 2 and p[0] == p[-1] == '"':
        p = p[1:-1]
    # cmd treats "" as a literal quote inside a quoted string.
    p = p.replace('"', '""')
    return f'"{p}"'


def _rewrite_segment(segment: str) -> tuple[str, list[str]]:
    s = segment.strip()
    if not s:
        return s, []
    notes: list[str] = []
    toks = _tokens(s)
    if not toks:
        return s, notes
    head = _unquote(toks[0]).lower()

    # mkdir -p a b
    if head == "mkdir" and len(toks) >= 2 and toks[1] in ("-p", "--parents"):
        paths = [_unquote(t) for t in toks[2:] if not t.startswith("-")]
        if not paths:
            return "echo no_paths", ["mkdir -p (empty)"]
        parts = []
        for p in paths:
            win_p = p.replace("/", "\\").rstrip("\\")
            # Parens so `mkdir -p dest && gcc` still runs gcc when dest exists.
            # Bare `if not exist … && gcc` skips gcc (IF consumes the line).
            parts.append(f'(if not exist {_q(win_p + os.sep)} mkdir {_q(win_p)})')
        notes.append("mkdir -p → if not exist mkdir")
        return " & ".join(parts), notes

    # rm -rf / rm -r / rm -f
    if head == "rm" and len(toks) >= 2:
        flags = {t for t in toks[1:] if t.startswith("-")}
        paths = [_unquote(t) for t in toks[1:] if not t.startswith("-")]
        recursive = any(f in flags or f.startswith("-") and ("r" in f or "R" in f) for f in flags)
        # -rf / -fr / --recursive
        joined_flags = "".join(flags)
        recursive = recursive or "r" in joined_flags.lower() or "--recursive" in flags
        if not paths:
            return s, notes
        bits = []
        for p in paths:
            win_p = p.replace("/", "\\").rstrip("\\")
            if recursive:
                bits.append(
                    f'if exist "{win_p}\\" (rmdir /s /q "{win_p}") else if exist "{win_p}" del /f /q "{win_p}"'
                )
            else:
                bits.append(f'del /f /q "{win_p}"')
        notes.append("rm → del/rmdir")
        return " & ".join(bits), notes

    if head in ("cp", "copy") and len(toks) >= 3:
        rec = any(t in ("-r", "-R", "-a", "--recursive") for t in toks[1:])
        paths = [_unquote(t) for t in toks[1:] if not t.startswith("-")]
        if len(paths) >= 2:
            src, dst = paths[0], paths[-1]
            if rec:
                notes.append("cp -r → xcopy")
                return f'xcopy /e /i /y {_q(src)} {_q(dst)}', notes
            notes.append("cp → copy")
            return f"copy /y {_q(src)} {_q(dst)}", notes

    if head in ("mv", "move") and len(toks) >= 3:
        paths = [_unquote(t) for t in toks[1:] if not t.startswith("-")]
        if len(paths) >= 2:
            notes.append("mv → move")
            return f"move /y {_q(paths[0])} {_q(paths[-1])}", notes

    if head == "cat" and len(toks) >= 2 and not any(t.startswith("-") for t in toks[1:]):
        files = [_unquote(t) for t in toks[1:]]
        notes.append("cat → type")
        return " & ".join(f"type {_q(f)}" for f in files), notes

    if head == "ls":
        paths = [_unquote(t) for t in toks[1:] if not t.startswith("-")]
        notes.append("ls → dir")
        if paths:
            return " & ".join(f"dir {_q(p)}" for p in paths), notes
        return "dir", notes

    if head == "pwd" and len(toks) == 1:
        notes.append("pwd → cd")
        return "cd", notes

    if head == "export" and len(toks) >= 2:
        assign = _unquote(" ".join(toks[1:]))
        notes.append("export → set")
        return f"set {assign}", notes

    if head == "touch" and len(toks) >= 2:
        paths = [_unquote(t) for t in toks[1:] if not t.startswith("-")]
        bits = [f'if not exist {_q(p)} type nul > {_q(p)}' for p in paths]
        notes.append("touch → type nul")
        return " & ".join(bits) if bits else s, notes

    if head == "true" and len(toks) == 1:
        notes.append("true → cd .")
        return "cd .", notes

    if head == "false" and len(toks) == 1:
        notes.append("false → cmd /c exit 1")
        return "cmd /c exit 1", notes

    if head in ("which",) or (head == "command" and len(toks) >= 3 and toks[1] == "-v"):
        name = _unquote(toks[-1])
        notes.append("which → where")
        return f"where {name}", notes

    if head == "chmod":
        notes.append("chmod ignored on Windows host")
        return "", notes

    if head == "grep":
        rg = _find_rg()
        pattern = ""
        grep_files: list[str] = []
        rest = [_unquote(t) for t in toks[1:]]
        cleaned: list[str] = []
        for t in rest:
            if t.startswith("-") and t not in ("-e",):
                continue
            if t == "-e":
                continue
            cleaned.append(t)
        if cleaned:
            pattern = cleaned[0]
            grep_files = cleaned[1:]
        if pattern and rg:
            notes.append("grep → rg")
            file_bits = " ".join(_q(f) for f in grep_files)
            return f'"{rg}" -n {_q(pattern)} {file_bits}'.strip(), notes
        if pattern:
            # Literal only — grep regex is not findstr.
            notes.append("grep → findstr (literal)")
            file_bits = " ".join(_q(f) for f in grep_files)
            if file_bits:
                return f"findstr /n /c:{_q(pattern)} {file_bits}".strip(), notes
            # No file operands = stdin (piped grep), not a recursive * walk.
            return f"findstr /n /c:{_q(pattern)}", notes

    if head in ("head", "tail"):
        n = 10
        files: list[str] = []
        i = 1
        while i < len(toks):
            t = toks[i]
            if t in ("-n", "--lines") and i + 1 < len(toks):
                with suppress(ValueError):
                    n = max(1, int(_unquote(toks[i + 1])))
                i += 2
                continue
            if t.startswith("-") and t[1:].isdigit():
                n = max(1, int(t[1:]))
                i += 1
                continue
            if not t.startswith("-"):
                files.append(_unquote(t))
            i += 1
        if files:
            notes.append(f"{head} → python slice")
            return _python_line_slice(files[0], n, tail=(head == "tail")), notes

    if head == "find":
        name_pat = ""
        start = "."
        rest = [_unquote(t) for t in toks[1:]]
        i = 0
        while i < len(rest):
            if rest[i] == "-name" and i + 1 < len(rest):
                name_pat = rest[i + 1]
                i += 2
                continue
            if not rest[i].startswith("-") and start == ".":
                start = rest[i]
            i += 1
        if name_pat:
            notes.append("find -name → dir /s /b")
            win_start = start.replace("/", "\\").rstrip("\\") or "."
            return f"dir /s /b {_q(win_start + '\\' + name_pat)}", notes

    if head == "test" and len(toks) >= 3 and toks[1] in ("-f", "-e"):
        notes.append("test -f → if exist")
        return (
            f"if exist {_q(_unquote(toks[2]))} (echo exists) else (exit /b 1)",
            notes,
        )
    if head == "[" and "-f" in toks:
        path_tok = ""
        for i, t in enumerate(toks):
            if t == "-f" and i + 1 < len(toks):
                path_tok = _unquote(toks[i + 1]).rstrip("]")
                break
        if path_tok:
            notes.append("[ -f → if exist")
            return (
                f"if exist {_q(path_tok)} (echo exists) else (exit /b 1)",
                notes,
            )

    return s, notes


def _python_line_slice(path: str, n: int, *, tail: bool) -> str:
    exe = sys.executable or "python"
    win_p = path.replace("/", "\\") if ("/" in path or os.name == "nt") else path
    # Keep the one-liner free of nested double quotes.
    op = f"p[-{int(n)}:]" if tail else f"p[:{int(n)}]"
    code = (
        "p=open(r'''"
        + win_p.replace("'''", "")
        + "''',encoding='utf-8',errors='replace').read().splitlines(True);"
        + f"print(''.join({op}),end='')"
    )
    return f"{_q(exe)} -c {_q(code)}"


def _find_rg() -> str:
    try:
        from remedy.core.rg_binary import find_rg

        path, _src = find_rg()
        return str(path) if path else ""
    except Exception:
        return ""

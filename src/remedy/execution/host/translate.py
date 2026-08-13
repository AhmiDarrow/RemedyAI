"""Deterministic POSIX → Windows-cmd rewrite for model-emitted shell strings.

Not a bash. Known-safe substitutions plus "leave it / diagnose" for the rest.
PowerShell payloads are *not* rewritten here — the runner sends them through
a temp ``.ps1`` and ``pwsh -File``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Operators we split on at top level (longest first).
_CHAIN_OPS = ("&&", "||", ">>", "2>&1", "2>", "1>", "&>", ">&", "|", ";", "&")

_UNTRANSLATABLE = re.compile(
    r"(?<!\$)\$\([^)]+\)|`[^`]+`|\$\{[^}]+\}",
)

# PowerShell-shaped — do not POSIX-rewrite.
_PS_HINT = re.compile(
    r"(?is)("
    r"\b(?:Get|Set|New|Remove|Invoke|Write|Select|Where|ForEach|Out|Add|Clear|"
    r"ConvertTo|ConvertFrom|Import|Export|Start|Stop|Test|Measure)-[A-Za-z]"
    r"|\$_\b"
    r"|\$PSVersionTable"
    r"|\$env:[A-Za-z]"
    r"|\bparam\s*\("
    r"|@['\"]"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\b"
    r"|\s-(?:eq|ne|gt|lt|ge|le|match|like|contains|notmatch)\b"
    r")"
)


@dataclass
class TranslateResult:
    text: str
    changed: bool = False
    notes: list[str] = field(default_factory=list)
    untranslatable: bool = False


def looks_like_powershell(command: str) -> bool:
    """True when the string is PowerShell, not POSIX/cmd."""
    cmd = (command or "").strip()
    if not cmd:
        return False
    return bool(_PS_HINT.search(cmd))


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
    out_segs: list[str] = []
    changed = rewritten != raw
    for seg, op in parts:
        new_seg, seg_notes = _rewrite_segment(seg)
        if new_seg != seg:
            changed = True
        notes.extend(seg_notes)
        out_segs.append(new_seg)
        if op:
            # Keep cmd-supported && || | ;  — map bash &> already handled
            if op == ";":
                out_segs.append(" & ")
            else:
                out_segs.append(f" {op} " if op in ("&&", "||", "|", "&") else f" {op} ")
    text = "".join(out_segs).strip()
    # Collapse leftover doubled spaces around ops
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
    if p.startswith('"') and p.endswith('"'):
        return p
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
            parts.append(f'if not exist "{win_p}\\" mkdir "{win_p}"')
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
        # No-op on Windows for +x style agent noise
        notes.append("chmod ignored on Windows host")
        return "cd .", notes

    if head == "grep":
        rg = _find_rg()
        pattern = ""
        files: list[str] = []
        rest = [_unquote(t) for t in toks[1:]]
        # drop common flags
        cleaned: list[str] = []
        for t in rest:
            if t.startswith("-") and t not in ("-e",):
                continue
            if t == "-e":
                continue
            cleaned.append(t)
        if cleaned:
            pattern = cleaned[0]
            files = cleaned[1:]
        if rg and pattern:
            notes.append("grep → rg")
            file_bits = " ".join(_q(f) for f in files)
            return f'"{rg}" -n {_q(pattern)} {file_bits}'.strip(), notes

    return s, notes


def _find_rg() -> str:
    try:
        from remedy.core.rg_binary import find_rg

        p = find_rg()
        return str(p) if p else ""
    except Exception:
        return ""

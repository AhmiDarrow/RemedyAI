"""Write scratch scripts and run them with ``-File`` / ``/c`` — never ``-Command``."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

_PS_WRAPPER = re.compile(
    r"(?is)^\s*(?:(?:[A-Za-z]:\\)?(?:[^\s\"']*\\)?)?(?:powershell|pwsh)(?:\.exe)?"
    r"(?P<flags>(?:\s+-[A-Za-z][\w:]*)*)"
    r"\s+-(?:Command|c)\s+"
    r"(?P<body>.+)$"
)

_ENCODED = re.compile(
    r"(?is)\b(?:powershell|pwsh)(?:\.exe)?\b.*-(?:e|ec|encodedcommand|encoded)\b"
)


@dataclass
class ScriptLaunch:
    argv: list[str]
    path: Path
    lang: str
    body: str


def is_encoded_powershell(command: str) -> bool:
    return bool(_ENCODED.search(command or ""))


def extract_powershell_payload(command: str) -> str | None:
    """Return the script body if *command* is inline PowerShell.

    ``powershell -Command '…'`` / ``pwsh -c …`` unwrap to the inner body.
    A bare cmdlet string is returned as-is. EncodedCommand returns None
    (security — leave the original for the write jail).
    """
    cmd = (command or "").strip()
    if not cmd or is_encoded_powershell(cmd):
        return None
    m = _PS_WRAPPER.match(cmd)
    if m:
        return _strip_ps_quotes(m.group("body").strip())
    # Bare PowerShell (detected by caller via looks_like_powershell)
    if re.match(r"(?i)^\s*(?:powershell|pwsh)(?:\.exe)?\s*$", cmd):
        return None
    return cmd


def _strip_ps_quotes(body: str) -> str:
    b = body.strip()
    if len(b) >= 2 and b[0] == b[-1] and b[0] in ('"', "'"):
        inner = b[1:-1]
        # powershell -Command "..." often doubles quotes
        return inner.replace('""', '"')
    return b


def scratch_script_path(
    lang: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> Path:
    """Unique path under ``.remedy-build/tmp`` (or *scratch_dir*)."""
    ext = { "pwsh": ".ps1", "powershell": ".ps1", "cmd": ".cmd", "python": ".py" }.get(
        (lang or "pwsh").lower(), ".ps1"
    )
    name = f"host_{uuid.uuid4().hex[:12]}{ext}"
    if scratch_dir is not None:
        d = Path(scratch_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / name
    try:
        from remedy.core.build_ledger import build_tmp_script_path

        return build_tmp_script_path(name, project_path)
    except Exception:
        d = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "remedy-host"
        d.mkdir(parents=True, exist_ok=True)
        return d / name


_MAX_SCRIPT_CHARS = 1_000_000


def write_script(lang: str, body: str, path: Path) -> Path:
    """Write *body* with the encoding the host interpreter expects."""
    if len(body or "") > _MAX_SCRIPT_CHARS:
        raise ValueError(f"script body exceeds {_MAX_SCRIPT_CHARS} characters")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body if body.endswith("\n") else body + "\n"
    kind = (lang or "pwsh").lower()
    if kind in ("pwsh", "powershell", "ps1"):
        # UTF-8 BOM so Windows PowerShell 5.1 parses non-ASCII
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def launch_script(
    lang: str,
    body: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> ScriptLaunch:
    """Write the script and return argv that runs it (never ``-Command``)."""
    kind = (lang or "pwsh").lower()
    path = scratch_script_path(kind, scratch_dir=scratch_dir, project_path=project_path)
    write_script(kind, body, path)
    if kind in ("pwsh", "powershell", "ps1"):
        exe = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
        argv = [
            exe,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
        ]
        return ScriptLaunch(argv=argv, path=path, lang="pwsh", body=body)
    if kind in ("cmd", "bat", "batch"):
        exe = shutil.which("cmd") or "cmd.exe"
        argv = [exe, "/c", str(path)]
        return ScriptLaunch(argv=argv, path=path, lang="cmd", body=body)
    # python
    import sys

    argv = [sys.executable, str(path)]
    return ScriptLaunch(argv=argv, path=path, lang="python", body=body)

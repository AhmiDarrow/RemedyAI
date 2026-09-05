"""Scratch host_* script launch helpers over Zig ``host_op_prepare``.

Internal. Public imports go through ``host_binding``. Scriptfile write lives in
Zig; this module ages out leftovers and builds argv (including real CPython
for ``python`` scripts so the Desktop sidecar is never re-launched).
"""
from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from ._core import HostError
from ._ir import host_op_prepare

_MAX_SCRIPT_CHARS = 1_000_000
_HOST_SCRIPT_PREFIX = "host_"
_HOST_SCRIPT_MAX_AGE_S = 3600.0


@dataclass
class ScriptLaunch:
    argv: list[str]
    path: Path
    lang: str
    body: str


def cleanup_host_script(path: Path | None) -> None:
    """Delete a scratch host_* script after it has run."""
    if path is None:
        return
    p = Path(path)
    if not p.is_file() or not p.name.startswith(_HOST_SCRIPT_PREFIX):
        return
    with suppress(OSError):
        p.unlink()


def age_out_host_scripts(
    scratch_dir: Path | None = None,
    *,
    max_age_s: float = _HOST_SCRIPT_MAX_AGE_S,
) -> int:
    """Remove leftover host_* scratch files older than *max_age_s*."""
    import time

    roots: list[Path] = []
    if scratch_dir is not None:
        roots.append(Path(scratch_dir))
    else:
        for envk in ("TEMP", "TMP"):
            raw = os.environ.get(envk)
            if raw:
                roots.append(Path(raw) / "remedy-host")
        roots.append(Path(".remedy-build") / "tmp")
    removed = 0
    now = time.time()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            kids = list(root.iterdir())
        except OSError:
            continue
        for kid in kids:
            if not kid.is_file() or not kid.name.startswith(_HOST_SCRIPT_PREFIX):
                continue
            try:
                if now - kid.stat().st_mtime > max_age_s:
                    kid.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def launch_script(
    lang: str,
    body: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> ScriptLaunch:
    """Write a scratch script via Zig ``host_op_prepare`` (never ``-Command``).

    Python argv uses ``python_cmd_for_subprocess`` so the Desktop sidecar is
    never re-launched as the interpreter (Zig PATH resolve alone is not enough).
    """
    text = body or ""
    if len(text) > _MAX_SCRIPT_CHARS:
        raise ValueError(f"script body exceeds {_MAX_SCRIPT_CHARS} characters")
    kind = (lang or "pwsh").lower()
    if kind in ("powershell", "ps1"):
        kind = "pwsh"
    try:
        prep = host_op_prepare(
            op={"kind": "script", "lang": kind, "body": text},
            scratch_dir=str(scratch_dir) if scratch_dir else None,
            project_path=str(project_path) if project_path else None,
        )
    except HostError as exc:
        if kind == "python":
            raise ValueError(
                "No real Python interpreter for host_script "
                "(Desktop sidecar is not CPython). Set REMEDY_PYTHON."
            ) from exc
        raise ValueError(f"host script prepare failed: {exc}") from exc
    script = prep.get("script_path") or None
    if str(prep.get("kind") or "") != "script" or not script:
        raise ValueError("host script prepare did not produce a script file")
    path = Path(str(script))
    age_out_host_scripts(path.parent)
    argv = [str(a) for a in (prep.get("argv") or []) if str(a)]
    ir_raw = prep.get("ir")
    ir_lang = ""
    if isinstance(ir_raw, dict):
        ir_lang = str(ir_raw.get("lang") or "")
    out_lang = ir_lang or kind
    if kind == "python" or out_lang == "python":
        from remedy.core.build_python import python_cmd_for_subprocess

        py = python_cmd_for_subprocess()
        if not py:
            cleanup_host_script(path)
            raise ValueError(
                "No real Python interpreter for host_script "
                "(Desktop sidecar is not CPython). Set REMEDY_PYTHON."
            )
        argv = [*py, str(path)]
        out_lang = "python"
    return ScriptLaunch(argv=argv, path=path, lang=out_lang, body=text)


__all__ = [
    "ScriptLaunch",
    "age_out_host_scripts",
    "cleanup_host_script",
    "launch_script",
]

"""Last-good host dialect for this machine — sibling to RMB last_good_fit."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic

_DIALECT_REL = Path("host") / "dialect.json"


@dataclass
class HostDialect:
    host: str = "cmd"
    python_cmd: str = ""
    git_cmd: str = ""
    rg_cmd: str = ""
    curl_kind: str = ""  # real | missing
    pwsh_cmd: str = ""
    last_good_verify: str = ""
    successes: int = 0
    last_success_at: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostDialect:
        if not isinstance(data, dict):
            return cls()
        notes = data.get("notes") or []
        if not isinstance(notes, list):
            notes = []
        return cls(
            host=str(data.get("host") or "cmd"),
            python_cmd=str(data.get("python_cmd") or ""),
            git_cmd=str(data.get("git_cmd") or ""),
            rg_cmd=str(data.get("rg_cmd") or ""),
            curl_kind=str(data.get("curl_kind") or ""),
            pwsh_cmd=str(data.get("pwsh_cmd") or ""),
            last_good_verify=str(data.get("last_good_verify") or ""),
            successes=int(data.get("successes") or 0),
            last_success_at=str(data.get("last_success_at") or ""),
            notes=[str(n) for n in notes][:12],
        )


def dialect_path(home: str | Path | None = None) -> Path:
    base = _home(home)
    return base / _DIALECT_REL


def load_dialect(home: str | Path | None = None) -> HostDialect:
    path = dialect_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _probe_cached(home)
    d = HostDialect.from_dict(raw if isinstance(raw, dict) else None)
    # Fill any empty probe fields without clobbering last-good. Probe lazily:
    # load_dialect sits on hot paths (resolve_which per host command,
    # record_success, the per-turn system-prompt inject) and a healthy
    # dialect.json needs no which/glob sweeps at all.
    if (
        not d.python_cmd
        or not _python_cmd_usable(d.python_cmd)
        or not d.git_cmd
        or not d.rg_cmd
        or d.rg_cmd.startswith("(")
        or not d.curl_kind
        or not d.pwsh_cmd
        or not d.host
    ):
        probed = _probe_cached(home)
        if not d.python_cmd or not _python_cmd_usable(d.python_cmd):
            # Sidecar / Store stub stamped by an older probe — heal in memory.
            d.python_cmd = probed.python_cmd
        if not d.git_cmd:
            d.git_cmd = probed.git_cmd
        if not d.rg_cmd or d.rg_cmd.startswith("("):
            # Heal a leftover `str((Path, source))` tuple from older probes.
            d.rg_cmd = probed.rg_cmd
        if not d.curl_kind:
            d.curl_kind = probed.curl_kind
        if not d.pwsh_cmd:
            d.pwsh_cmd = probed.pwsh_cmd
        if not d.host:
            d.host = probed.host
    return d


# A host permanently missing a tool (no pwsh installed → pwsh_cmd stays "")
# would otherwise re-probe on every load; cache probe results briefly so the
# hot paths stay cheap while a newly installed tool is still picked up.
_PROBE_TTL_S = 300.0
_probe_cache: dict[str, tuple[float, HostDialect]] = {}


def _probe_cached(home: str | Path | None) -> HostDialect:
    key = str(_home(home))
    now = time.monotonic()
    hit = _probe_cache.get(key)
    if hit is not None and now - hit[0] < _PROBE_TTL_S:
        return hit[1]
    probed = probe_host_dialect(home=home, persist=False)
    _probe_cache[key] = (now, probed)
    return probed


def save_dialect(dialect: HostDialect, home: str | Path | None = None) -> Path:
    path = dialect_path(home)
    write_json_atomic(path, dialect.to_dict())
    return path


def _python_cmd_usable(path: str) -> bool:
    from remedy.core.build_python import is_usable_host_python

    return is_usable_host_python(path)


def probe_host_dialect(
    *,
    home: str | Path | None = None,
    persist: bool = False,
) -> HostDialect:
    """Cheap PATH probe — no network, no long commands."""
    python = ""
    try:
        from remedy.core.build_python import host_python_executable

        python = host_python_executable()
    except Exception:
        python = ""
    if not python:
        for name in ("python", "python3", "py"):
            found = shutil.which(name) or ""
            if found and _python_cmd_usable(found):
                python = found
                break
    git = shutil.which("git") or ""
    pwsh = shutil.which("pwsh") or ""
    curl = shutil.which("curl") or ""
    rg = ""
    try:
        from remedy.core.rg_binary import find_rg

        path, _src = find_rg(home_dir=home)
        if path:
            raw = str(path)
            posix = Path(path).as_posix()
            # Keep Unix probe paths as POSIX (`/usr/bin/rg`), not `\usr\bin\rg` on NT.
            rg = posix if posix.startswith("/") else raw
        else:
            rg = shutil.which("rg") or ""
    except Exception:
        rg = shutil.which("rg") or ""
    d = HostDialect(
        host="cmd" if os.name == "nt" else "posix",
        python_cmd=python or "",
        git_cmd=git,
        rg_cmd=rg or "",
        curl_kind="real" if curl else "missing",
        pwsh_cmd=pwsh,
    )
    if persist:
        save_dialect(d, home)
    return d


def record_success(
    command: str,
    *,
    home: str | Path | None = None,
    note: str = "",
) -> HostDialect:
    d = load_dialect(home)
    d.successes = int(d.successes or 0) + 1
    d.last_success_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    low = (command or "").lower()
    if any(k in low for k in ("pytest", "py_compile", "cargo test", "npm test", "go test")):
        d.last_good_verify = (command or "").strip()[:240]
    if note:
        notes = [note, *[n for n in d.notes if n != note]]
        d.notes = notes[:12]
    save_dialect(d, home)
    return d


def format_dialect_line(dialect: HostDialect | None = None, home: str | Path | None = None) -> str:
    """One-line inject: this PC's host, not a tutorial."""
    d = dialect or load_dialect(home)
    bits = [f"Host bridge: {d.host or 'cmd'}"]
    if d.python_cmd:
        bits.append(f"python={d.python_cmd}")
    if d.rg_cmd:
        bits.append("rg=yes")
    if d.curl_kind:
        bits.append(f"curl={d.curl_kind}")
    if d.last_good_verify:
        bits.append(f"last_verify={d.last_good_verify[:80]}")
    bits.append("prefer host_run(argv) / host_mkdir / host_script over quoted bash")
    return " · ".join(bits)


def _home(home: str | Path | None) -> Path:
    if home:
        return Path(home).expanduser()
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        env = (os.environ.get("REMEDY_HOME") or "").strip()
        return Path(env or "~/.remedy").expanduser()

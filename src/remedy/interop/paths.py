"""On-disk layout under {home}/interop/."""

from __future__ import annotations

import os
from pathlib import Path

from remedy.home import default_home

DIR_NAME = "interop"


def resolve_home(home: str | Path | None = None) -> Path:
    if home:
        return Path(home).expanduser().resolve()
    env = os.environ.get("REMEDY_HOME")
    if env:
        return Path(env).expanduser().resolve()
    try:
        from remedy.interfaces.config import get_home_dir

        return Path(get_home_dir()).expanduser().resolve()
    except Exception:
        return default_home().expanduser().resolve()


def interop_dir(home: str | Path | None = None) -> Path:
    path = resolve_home(home) / DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def peers_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "peers.json"


def tasks_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "tasks.json"


def audit_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "audit.jsonl"


def inbox_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "inbox.jsonl"


def rate_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "rate.json"


def token_path(home: str | Path | None = None) -> Path:
    return interop_dir(home) / "inbound_token"

"""Borrow a provider key from the *live* install for a teacher run.

The sandbox has its own empty home, so a cloud reference run has no credentials
of its own. Rather than making the operator paste a key onto a command line
(where it lands in shell history and the process table), resolve it through
Remedy's own secure-store reader and hand it straight to the sandbox config.

Read-only: nothing here writes to the live home, and the key is never logged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))


def host_home() -> Path:
    """The operator's real Remedy home (not any sandbox)."""
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def host_provider_key(provider: str, *, home: Path | None = None) -> str:
    """Resolve the stored key for one provider, or "" when there is none."""
    from remedy.interfaces.config import load_config, resolve_provider_api_key

    base = home or host_home()
    cfg_path = base / "config.toml"
    cfg = load_config(cfg_path) if cfg_path.is_file() else {}
    try:
        return resolve_provider_api_key(cfg, provider, home=base) or ""
    except Exception:
        return ""


def host_llm_defaults(*, home: Path | None = None) -> tuple[str, str, str]:
    """``(provider, model, base_url)`` currently configured on the live install."""
    from remedy.interfaces.config import load_config

    base = home or host_home()
    cfg_path = base / "config.toml"
    cfg = load_config(cfg_path) if cfg_path.is_file() else {}
    return (
        str(cfg.get("llm_provider") or ""),
        str(cfg.get("llm_model") or ""),
        str(cfg.get("llm_base_url") or ""),
    )


def describe(provider: str) -> str:
    """Say whether a key is available without ever revealing it."""
    key = host_provider_key(provider)
    if not key:
        return f"no stored key for {provider}"
    return f"{provider} key found ({len(key)} chars, ...{key[-4:]})"

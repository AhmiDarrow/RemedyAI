"""Borrow a provider key from the operator's environment for a teacher run.

The sandbox has its own empty home, so a cloud reference run has no credentials
of its own. The key is taken from an environment variable, never from a command
line (where it would land in shell history and the process table):
``REMEDY_RIG_API_KEY``, or the provider's own ``<PROVIDER>_API_KEY``
(``ANTHROPIC_API_KEY``, ``XAI_API_KEY``, ``OPENAI_API_KEY`` and so on).

The live install keeps its keys in the system secure store, which only the
runtime reads. This harness does not reach into it.

Read-only: nothing here writes to the live home, and the key is never logged.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path


def host_home() -> Path:
    """The operator's real Remedy home (not any sandbox)."""
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def host_provider_key(provider: str, *, home: Path | None = None) -> str:
    """The key for one provider from the environment, or "" when there is none."""
    _ = home
    name = re.sub(r"[^A-Z0-9]+", "_", provider.strip().upper()).strip("_")
    for var in ("REMEDY_RIG_API_KEY", f"{name}_API_KEY" if name else ""):
        if var and os.environ.get(var, "").strip():
            return os.environ[var].strip()
    return ""


def host_llm_defaults(*, home: Path | None = None) -> tuple[str, str, str]:
    """``(provider, model, base_url)`` currently configured on the live install."""
    cfg_path = (home or host_home()) / "config.toml"
    try:
        cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        cfg = {}
    return (
        str(cfg.get("llm_provider") or ""),
        str(cfg.get("llm_model") or ""),
        str(cfg.get("llm_base_url") or ""),
    )


def describe(provider: str) -> str:
    """Say whether a key is available without ever revealing it."""
    key = host_provider_key(provider)
    if not key:
        return f"no key for {provider} in the environment"
    return f"{provider} credentials available"

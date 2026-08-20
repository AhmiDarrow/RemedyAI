"""Detect when Remedy is running in RMB local-agent mode.

When RMB is the chat provider (or the managed host on :8787 is up), vision
SmolVLM must not load. Briefs/nano/image path handling go through the single
RMB chat model for long coding sessions.

``llamacpp`` alone is NOT RMB — it may be the vision stack (:8740). Exclusive
host rules use provider ``rmb`` and/or base URL port 8787.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _loopback(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        host = ""
    return host in ("localhost", "0.0.0.0", "::1") or host.startswith("127.")


def is_rmb_base_url(base_url: str | None) -> bool:
    """True when URL is the managed RMB chat host (port 8787 / rmb host)."""
    u = (base_url or "").strip()
    if not u:
        return False
    try:
        parts = urlsplit(u)
        port = parts.port
        host = (parts.hostname or "").lower()
    except Exception:
        port = None
        host = ""
        parts = None
    if port == 8787:
        return True
    # Explicit brand hostnames only — no path substring matching
    if host in ("rmb", "rmb.local", "muscle-bridge", "musclebridge"):
        return True
    # Bare ":8787" forms without parseable port
    low = u.lower()
    return bool(":8787/" in low or low.rstrip("/").endswith(":8787"))


def is_rmb_provider(provider: str | None, base_url: str | None = None) -> bool:
    """True when the chat adapter is RMB (not generic llamacpp / vision)."""
    p = (provider or "").strip().lower()
    if p == "rmb":
        return True
    # llamacpp only counts as RMB when pointed at the managed chat port
    if p in ("llamacpp", "custom", "local", "openai") and is_rmb_base_url(base_url):
        return True
    return bool(is_rmb_base_url(base_url))


def is_local_agent_mode(
    cfg: dict[str, Any] | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when the *chat* path is the managed RMB host.

    Only when the active chat provider/URL is RMB — not merely because
    rmb.json exists or a generic llama.cpp host is configured.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    prov = (provider or cfg.get("llm_provider") or "").strip().lower()
    url = (base_url or cfg.get("llm_base_url") or "").strip()

    if prov == "rmb":
        return True
    if is_rmb_provider(prov, url):
        return True

    # custom/local explicitly aimed at configured RMB base
    rmb_raw = cfg.get("rmb")
    rmb: dict[str, Any] = rmb_raw if isinstance(rmb_raw, dict) else {}
    rmb_url = str(rmb.get("base_url") or "").strip()
    if prov in ("custom", "local") and url and rmb_url:
        if url.rstrip("/") == rmb_url.rstrip("/") or is_rmb_base_url(url):
            return True

    return False


def rmb_chat_base_url(cfg: dict[str, Any] | None = None) -> str:
    """Canonical OpenAI /v1 base for the RMB chat host."""
    cfg = cfg if isinstance(cfg, dict) else {}
    rmb_raw = cfg.get("rmb")
    rmb: dict[str, Any] = rmb_raw if isinstance(rmb_raw, dict) else {}
    if rmb.get("base_url"):
        return str(rmb["base_url"]).rstrip("/")
    try:
        from remedy.runtime.rmb.config import load_rmb_json, merge_state

        st = merge_state(load_rmb_json(cfg.get("home_dir")))
        return str(st.get("base_url") or "http://127.0.0.1:8787/v1").rstrip("/")
    except Exception:
        return "http://127.0.0.1:8787/v1"


def rmb_server_running(home_dir: str | Path | None = None) -> bool:
    """True when the managed RMB llama-server process/port is healthy."""
    try:
        from remedy.runtime.rmb.service import is_running, is_starting

        if is_starting():
            return True
        return bool(is_running(home_dir, force=False, require_http=True))
    except Exception:
        return False


def should_skip_vision_stack(cfg: dict[str, Any] | None = None) -> bool:
    """When RMB owns GPU/local host, never *start* SmolVLM.

    True if:
    - chat provider is RMB, or
    - RMB server is currently running / starting, or
    - rmb.json marks vision_suspended (set on start until stop)
    """
    if is_local_agent_mode(cfg):
        return True
    try:
        from remedy.runtime.rmb.config import load_rmb_json, merge_state
        from remedy.runtime.rmb.service import is_starting, managed_process_alive

        home = None
        if isinstance(cfg, dict):
            home = cfg.get("home_dir")
        st = merge_state(load_rmb_json(home))
        if st.get("vision_suspended"):
            return True
        if is_starting() or managed_process_alive():
            return True
    except Exception:
        pass
    return bool(rmb_server_running(cfg.get("home_dir") if isinstance(cfg, dict) else None))


def force_path_only_images(
    cfg: dict[str, Any] | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when images must be file-path text (no native multimodal / no Smol).

    Only for local RMB chat — cloud providers keep native vision even if
    Smol is suspended because RMB owns the GPU.
    """
    if is_local_agent_mode(cfg, provider=provider, base_url=base_url):
        return True
    p = (provider or "").strip().lower()
    if not p and isinstance(cfg, dict):
        p = str(cfg.get("llm_provider") or "").strip().lower()
    url = base_url
    if not url and isinstance(cfg, dict):
        url = str(cfg.get("llm_base_url") or "")
    return is_rmb_provider(p, url)


def harness_pcts_for_local_agent() -> tuple[float, float]:
    """Earlier soft/strong compress for 8k–16k local windows (endless coding)."""
    return 0.55, 0.78


def silent_context_for_local_agent(
    cfg: dict[str, Any] | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when context management must be invisible (no user-facing compress talk).

    RMB sessions: harness prune/offload/Session Brief run mechanically — the user
    never sees compress nudges. Remedy just remembers.
    """
    if is_local_agent_mode(cfg, provider=provider, base_url=base_url):
        return True
    prov = (provider or "").strip().lower()
    if not prov and isinstance(cfg, dict):
        prov = str(cfg.get("llm_provider") or "").strip().lower()
    url = base_url
    if not url and isinstance(cfg, dict):
        url = str(cfg.get("llm_base_url") or "")
    return is_rmb_provider(prov, url)

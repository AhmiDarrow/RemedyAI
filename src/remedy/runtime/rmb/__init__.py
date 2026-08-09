"""RMB — Remedy Muscle Bridge (local llama.cpp chat host).

Product name in UI: **RMB**. Implementation: managed ``llama-server`` on
loopback with a coding/tool-oriented GGUF — not the retired spatial RMB4 format.
"""

from __future__ import annotations

from remedy.runtime.rmb.config import (
    DEFAULT_CHAT_PORT,
    DEFAULT_HOST,
    load_rmb_json,
    rmb_home,
    save_rmb_json,
)
from remedy.runtime.rmb.mode import (
    force_path_only_images,
    harness_pcts_for_local_agent,
    is_local_agent_mode,
    is_rmb_base_url,
    is_rmb_provider,
    rmb_chat_base_url,
    rmb_server_running,
    should_skip_vision_stack,
    silent_context_for_local_agent,
)
from remedy.runtime.rmb.service import (
    ensure_rmb_server,
    ensure_rmb_watchdog,
    get_rmb_status,
    start_rmb_server,
    stop_rmb_server,
    wait_rmb_ready,
    wake_rmb_async,
)

__all__ = [
    "DEFAULT_CHAT_PORT",
    "DEFAULT_HOST",
    "ensure_rmb_server",
    "ensure_rmb_watchdog",
    "force_path_only_images",
    "get_rmb_status",
    "harness_pcts_for_local_agent",
    "is_local_agent_mode",
    "is_rmb_base_url",
    "is_rmb_provider",
    "load_rmb_json",
    "rmb_chat_base_url",
    "rmb_home",
    "rmb_server_running",
    "save_rmb_json",
    "should_skip_vision_stack",
    "silent_context_for_local_agent",
    "start_rmb_server",
    "stop_rmb_server",
    "wait_rmb_ready",
    "wake_rmb_async",
]

"""Optional web tools (opt-in via config web_tools_enabled)."""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from remedy.core.errors import format_tool_error


def _web_enabled(runtime: Any) -> bool:
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if cfg.get("web_tools_enabled") is True:
            return True
    except Exception:
        pass
    return bool(getattr(getattr(runtime, "config", None), "web_tools_enabled", False))


def register_web_tools(runtime: Any) -> None:
    """Register web_fetch when enabled (or always register with runtime gate)."""

    async def web_fetch(url: str = "", max_chars: int = 50_000) -> str:
        """Fetch a URL as text (opt-in web tools)."""
        if not _web_enabled(runtime):
            return format_tool_error(
                "Web tools are disabled. Enable web_tools_enabled in Settings/config.",
                code="WEB_DISABLED",
                tool_name="web_fetch",
                suggestion="Set web_tools_enabled: true in config, then retry.",
            )
        u = (url or "").strip()
        if not u.startswith(("http://", "https://")):
            return format_tool_error(
                "url must start with http:// or https://",
                code="BAD_URL",
                tool_name="web_fetch",
            )
        try:
            cap = max(1000, min(200_000, int(max_chars or 50_000)))
        except (TypeError, ValueError):
            cap = 50_000
        req = Request(
            u,
            headers={"User-Agent": "RemedyAI-WebFetch/0.13", "Accept": "text/*,application/json,*/*"},
        )
        try:
            with urlopen(req, timeout=25) as resp:  # noqa: S310 — user-opt-in tool
                raw = resp.read(cap + 1)
                charset = resp.headers.get_content_charset() or "utf-8"
        except HTTPError as e:
            return format_tool_error(
                f"HTTP {e.code}: {e.reason}",
                code="HTTP_ERROR",
                tool_name="web_fetch",
            )
        except URLError as e:
            return format_tool_error(
                f"Network error: {e.reason}",
                code="NETWORK_ERROR",
                tool_name="web_fetch",
            )
        except Exception as e:
            return format_tool_error(str(e), code="FETCH_ERROR", tool_name="web_fetch")

        text = raw.decode(charset, errors="replace")
        if len(raw) > cap:
            text = text[:cap] + f"\n…[truncated at {cap} chars]"
        return f"URL: {u}\n\n{text}"

    runtime.tool_registry.register_builtin_handler(
        "web_fetch",
        "Fetch an HTTP(S) URL as text (opt-in: config web_tools_enabled=true). "
        "Use for docs/APIs when online research is needed.",
        web_fetch,
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 50000)",
                    "default": 50000,
                },
            },
            "required": ["url"],
        },
    )

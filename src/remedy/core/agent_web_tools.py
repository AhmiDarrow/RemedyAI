"""Optional web tools (opt-in via config web_tools_enabled)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
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


def _host_is_blocked(hostname: str) -> bool:
    """Block localhost / link-local / private / metadata endpoints (SSRF)."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in ("localhost", "metadata.google.internal", "metadata"):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    # Literal IPs
    try:
        ip = ipaddress.ip_address(host)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        pass
    # Resolve DNS and check all addresses
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # fail closed
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


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
        parsed = urlparse(u)
        if _host_is_blocked(parsed.hostname or ""):
            return format_tool_error(
                "Refused: private/localhost/metadata URLs are blocked (SSRF protection).",
                code="SSRF_BLOCKED",
                tool_name="web_fetch",
                suggestion="Use a public https URL, or read local files with file_read.",
            )
        try:
            cap = max(1000, min(200_000, int(max_chars or 50_000)))
        except (TypeError, ValueError):
            cap = 50_000
        req = Request(
            u,
            headers={
                "User-Agent": "RemedyAI-WebFetch/0.13",
                "Accept": "text/*,application/json,*/*",
            },
        )
        try:
            with urlopen(req, timeout=25) as resp:  # noqa: S310 — user-opt-in tool
                # Re-check final host after redirects
                final = resp.geturl() or u
                final_host = urlparse(final).hostname or ""
                if _host_is_blocked(final_host):
                    return format_tool_error(
                        "Refused: redirect landed on a private/localhost host.",
                        code="SSRF_BLOCKED",
                        tool_name="web_fetch",
                    )
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

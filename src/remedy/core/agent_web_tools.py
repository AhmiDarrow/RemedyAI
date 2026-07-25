"""Optional web tools (opt-in via config web_tools_enabled).

SSRF protection: resolve DNS once, require all A/AAAA to be public, connect to a
pinned public IP with the original Host/SNI (mitigates DNS rebinding). Redirects
are followed only after re-validating each hop the same way.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

from remedy.core.errors import format_tool_error

# Max redirect hops (owner still has full public-web fetch power when enabled).
_MAX_REDIRECTS = 8
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def _web_enabled(runtime: Any) -> bool:
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if cfg.get("web_tools_enabled") is True:
            return True
    except Exception:
        pass
    return bool(getattr(getattr(runtime, "config", None), "web_tools_enabled", False))


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


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
        return _ip_is_blocked(ip)
    except ValueError:
        pass
    # Resolve DNS and check all addresses
    try:
        public = _resolve_public_ips(host)
    except OSError:
        return True  # fail closed
    return not public


def _resolve_public_ips(hostname: str) -> list[str]:
    """Resolve hostname; return public IPs only. Empty if any addr is blocked or none.

    Fail closed: if *any* resolved address is non-public, return [] (rebinding /
    dual-stack private face). Callers treat empty as blocked.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return []
    try:
        literal = ipaddress.ip_address(host)
        if _ip_is_blocked(literal):
            return []
        return [str(literal)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            return []  # mixed public+private → fail closed
        s = str(ip)
        if s not in seen:
            seen.add(s)
            ips.append(s)
    return ips


def _prefer_connect_ip(ips: list[str]) -> str:
    """Prefer IPv4 for wider urllib/http.client compatibility, else first."""
    for ip in ips:
        try:
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address):
                return ip
        except ValueError:
            continue
    return ips[0]


def _read_capped(resp: http.client.HTTPResponse, cap: int) -> tuple[bytes, str | None]:
    """Read body up to cap+1 bytes (detect truncation)."""
    chunks: list[bytes] = []
    total = 0
    limit = cap + 1
    while total < limit:
        block = resp.read(min(65536, limit - total))
        if not block:
            break
        chunks.append(block)
        total += len(block)
    charset = None
    try:
        ctype = resp.getheader("Content-Type") or ""
        if "charset=" in ctype.lower():
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip("\"'")
    except Exception:
        charset = None
    return b"".join(chunks), charset


def _pinned_fetch(url: str, *, max_chars: int, timeout: float = 25.0) -> tuple[str, bytes, str]:
    """Fetch URL with DNS pin-on-resolve; return (final_url, raw_bytes, charset).

    Raises ValueError for SSRF / bad URL; OSError/URLError-like for network.
    """
    current = (url or "").strip()
    for _hop in range(_MAX_REDIRECTS + 1):
        if not current.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        parsed = urlparse(current)
        host = (parsed.hostname or "").strip()
        if not host:
            raise ValueError("URL missing hostname")
        if _host_is_blocked(host):
            raise ValueError("SSRF_BLOCKED")
        public_ips = _resolve_public_ips(host)
        if not public_ips:
            raise ValueError("SSRF_BLOCKED")
        pinned = _prefer_connect_ip(public_ips)
        scheme = (parsed.scheme or "https").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        # Connect to pinned IP; Host / SNI use original hostname (HTTPS).
        headers = {
            "Host": host if not parsed.port else f"{host}:{parsed.port}",
            "User-Agent": "RemedyAI-WebFetch/0.13",
            "Accept": "text/*,application/json,*/*",
            "Connection": "close",
        }
        conn: http.client.HTTPConnection | None = None
        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                https = http.client.HTTPSConnection(
                    host,
                    port,
                    timeout=timeout,
                    context=ctx,
                )
                # Pin TCP to resolved public IP; TLS SNI/cert still use hostname.
                sock = socket.create_connection((pinned, port), timeout)
                try:
                    https.sock = ctx.wrap_socket(sock, server_hostname=host)
                except Exception:
                    sock.close()
                    raise
                conn = https
            else:
                conn = http.client.HTTPConnection(pinned, port, timeout=timeout)
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            if status in _REDIRECT_STATUS:
                loc = resp.getheader("Location") or ""
                resp.read()  # drain
                conn.close()
                if not loc:
                    raise ValueError(f"HTTP {status} redirect without Location")
                next_url = urljoin(current, loc)
                # Re-validate next hop before following (including private targets)
                next_host = urlparse(next_url).hostname or ""
                if _host_is_blocked(next_host) or not _resolve_public_ips(next_host):
                    raise ValueError("SSRF_BLOCKED_REDIRECT")
                current = next_url
                continue
            if status >= 400:
                reason = resp.reason or "error"
                _read_capped(resp, 500)
                conn.close()
                raise HTTPError(current, status, reason, resp.headers, None)
            raw, charset = _read_capped(resp, max_chars)
            conn.close()
            return current, raw, charset or "utf-8"
        except HTTPError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as e:
            if conn is not None:
                with suppress(Exception):
                    conn.close()
            raise URLError(str(e)) from e

    raise ValueError("Too many redirects")


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
        try:
            final_url, raw, charset = _pinned_fetch(u, max_chars=cap, timeout=25.0)
        except ValueError as e:
            msg = str(e)
            if "SSRF" in msg:
                return format_tool_error(
                    "Refused: private/localhost/metadata URLs are blocked (SSRF protection).",
                    code="SSRF_BLOCKED",
                    tool_name="web_fetch",
                    suggestion="Use a public https URL, or read local files with file_read.",
                )
            return format_tool_error(msg, code="BAD_URL", tool_name="web_fetch")
        except HTTPError as e:
            return format_tool_error(
                f"HTTP {e.code}: {e.reason}",
                code="HTTP_ERROR",
                tool_name="web_fetch",
            )
        except URLError as e:
            return format_tool_error(
                f"Network error: {getattr(e, 'reason', e)}",
                code="NETWORK_ERROR",
                tool_name="web_fetch",
            )
        except Exception as e:
            return format_tool_error(str(e), code="FETCH_ERROR", tool_name="web_fetch")

        text = raw.decode(charset or "utf-8", errors="replace")
        if len(raw) > cap:
            text = text[:cap] + f"\n…[truncated at {cap} chars]"
        shown = final_url if final_url != u else u
        return f"URL: {shown}\n\n{text}"

    runtime.tool_registry.register_builtin_handler(
        "web_fetch",
        "Fetch an HTTP(S) URL as text (opt-in: config web_tools_enabled=true). "
        "Use for docs/APIs when online research is needed. "
        "Private/localhost hosts are blocked (SSRF); public web remains fully available.",
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

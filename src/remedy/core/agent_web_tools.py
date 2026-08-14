"""Optional web tools (opt-in via config web_tools_enabled).

SSRF protection: resolve DNS once, require all A/AAAA to be public, connect to a
pinned public IP with the original Host/SNI (mitigates DNS rebinding). Redirects
are followed only after re-validating each hop the same way.
"""

from __future__ import annotations

import html as html_lib
import http.client
import ipaddress
import re
import socket
import ssl
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

from remedy.core.errors import format_tool_error

# Max redirect hops (owner still has full public-web fetch power when enabled).
_MAX_REDIRECTS = 8
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

# DuckDuckGo HTML result anchors (lite SERP — no JS).
_DDG_RESULT_A = re.compile(
    r'class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_DDG_RESULT_SNIP = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)>',
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _web_enabled(runtime: Any = None) -> bool:
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if cfg.get("web_tools_enabled") is True:
            return True
    except Exception:
        pass
    if runtime is None:
        return False
    return bool(getattr(getattr(runtime, "config", None), "web_tools_enabled", False))


def web_tools_enabled(runtime: Any = None) -> bool:
    """Public gate — Life research and tools share this opt-in."""
    return _web_enabled(runtime)


def search_public_web(
    query: str,
    *,
    max_results: int = 3,
    timeout: float = 12.0,
) -> list[dict[str, str]]:
    """Sync DuckDuckGo search. Empty if web tools are off or the net fails.

    Never raises. Does not fetch full pages. Same SSRF pin as web_search.
    """
    if not _web_enabled(None):
        return []
    q = (query or "").strip()[:400]
    if not q:
        return []
    try:
        n = max(1, min(5, int(max_results or 3)))
    except (TypeError, ValueError):
        n = 3
    search_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": q})
    try:
        _final, raw, charset = _pinned_fetch(
            search_url, max_chars=200_000, timeout=timeout
        )
        html = raw.decode(charset or "utf-8", errors="replace")
        return parse_ddg_html_results(html, max_results=n)
    except Exception:
        return []


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Refuse non-globally-routable addresses (fail closed).

    Uses ``not is_global`` so CGNAT (100.64/10), documentation, benchmark,
    and other non-public ranges are blocked even when ``is_private`` is False.
    Also keeps explicit private/loopback/link-local/reserved checks for clarity.
    """
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) is is_global and not is_loopback
    # in Python — unwrap before the public/private checks.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        (not ip.is_global)
        or ip.is_private
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
    if host in (
        "localhost",
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data",
    ):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    # IMDS-style DNS labels and wildcard-IP hosts (parity with computer router).
    if "metadata" in host.split("."):
        return True
    if host.endswith((".nip.io", ".sslip.io", ".xip.io")):
        return True
    if host.startswith("169.254."):
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
        # Block credentials in URL userinfo (user:pass@host) — never send them
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL_USERINFO_BLOCKED")
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
                next_parsed = urlparse(next_url)
                if next_parsed.username is not None or next_parsed.password is not None:
                    raise ValueError("URL_USERINFO_BLOCKED")
                next_host = next_parsed.hostname or ""
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


def _strip_tags(fragment: str) -> str:
    t = _TAG_RE.sub(" ", fragment or "")
    t = html_lib.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _unwrap_ddg_href(href: str) -> str:
    """DuckDuckGo lite wraps targets as /l/?uddg=<urlencoded> — unwrap when present."""
    h = (href or "").strip()
    if not h:
        return ""
    if h.startswith("//"):
        h = "https:" + h
    if h.startswith("/"):
        h = urljoin("https://html.duckduckgo.com", h)
    try:
        parsed = urlparse(h)
        qs = parse_qs(parsed.query or "")
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
    except Exception:
        pass
    return h


def parse_ddg_html_results(html: str, *, max_results: int = 5) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML SERP into {title, url, snippet} rows (testable, no net)."""
    body = html or ""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    snips = [_strip_tags(m.group(1)) for m in _DDG_RESULT_SNIP.finditer(body)]
    for i, m in enumerate(_DDG_RESULT_A.finditer(body)):
        if len(results) >= max(1, min(10, int(max_results or 5))):
            break
        url = _unwrap_ddg_href(m.group(1))
        title = _strip_tags(m.group(2))
        if not url or not title:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        # Skip DDG chrome / internal
        low = url.lower()
        if "duckduckgo.com" in low and "/l/?" not in low:
            # still allow unwrapped; drop pure ddg help pages
            if "/y.js" in low or "duckduckgo.com/html" in low:
                continue
        if url in seen:
            continue
        seen.add(url)
        snippet = snips[i] if i < len(snips) else ""
        results.append({"title": title[:200], "url": url[:500], "snippet": snippet[:400]})
    return results


async def _rail_page_text(url: str) -> str:
    """If the in-app browser can open *url*, return visible page text.

    Used when HTTP HTML is an empty shell (JS apps / bot walls). Failures
    return "" — web_fetch still has the HTTP extract.
    """
    import asyncio

    def _run() -> str:
        try:
            from remedy.core.computer.executor import get_computer_executor
            from remedy.core.computer.types import ComputerAction

            ex = get_computer_executor()
            nav = ex.run(ComputerAction.NAVIGATE, url=url, target="browser")
            if not isinstance(nav, dict):
                return ""
            page = ex.run(ComputerAction.PAGE_TEXT, target="browser")
            if not isinstance(page, dict):
                return ""
            text = str(page.get("text") or "").strip()
            title = str(page.get("title") or "").strip()
            if not text and page.get("ok") is False:
                return ""
            if title and text and title.lower() not in text[:200].lower():
                return f"# {title}\n\n{text}"
            return text or title
        except Exception:
            return ""

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return ""


def register_web_tools(runtime: Any) -> None:
    """Register web_fetch + web_search (opt-in via runtime gate)."""

    def _web_disabled_msg(tool_name: str) -> str:
        return format_tool_error(
            "Web tools are disabled. Enable with update_settings(web_tools_enabled=true) "
            f'or update_settings(setup="web tools"), then retry {tool_name}.',
            code="WEB_DISABLED",
            tool_name=tool_name,
            suggestion=(
                "Call update_settings(web_tools_enabled=true) for the user, then retry."
            ),
        )

    def _map_fetch_error(e: BaseException, *, tool_name: str) -> str:
        if isinstance(e, ValueError):
            msg = str(e)
            if "USERINFO" in msg:
                return format_tool_error(
                    "Refused: URLs must not include user:password@ credentials.",
                    code="URL_USERINFO_BLOCKED",
                    tool_name=tool_name,
                    suggestion="Pass a plain https URL without embedded credentials.",
                )
            if "SSRF" in msg:
                return format_tool_error(
                    "Refused: private/localhost/metadata URLs are blocked (SSRF protection).",
                    code="SSRF_BLOCKED",
                    tool_name=tool_name,
                    suggestion="Use a public https URL, or read local files with file_read.",
                )
            return format_tool_error(msg, code="BAD_URL", tool_name=tool_name)
        if isinstance(e, HTTPError):
            return format_tool_error(
                f"HTTP {e.code}: {e.reason}",
                code="HTTP_ERROR",
                tool_name=tool_name,
            )
        if isinstance(e, URLError):
            return format_tool_error(
                f"Network error: {getattr(e, 'reason', e)}",
                code="NETWORK_ERROR",
                tool_name=tool_name,
            )
        return format_tool_error(str(e), code="FETCH_ERROR", tool_name=tool_name)

    async def web_fetch(url: str = "", max_chars: int = 50_000) -> str:
        """Fetch a URL as readable text (opt-in web tools)."""
        if not _web_enabled(runtime):
            return _web_disabled_msg("web_fetch")
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
            final_url, raw, charset = _pinned_fetch(u, max_chars=max(cap * 4, 200_000), timeout=25.0)
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_fetch")

        shown = final_url if final_url != u else u
        text = raw.decode(charset or "utf-8", errors="replace")
        from remedy.core.html_extract import html_to_markdown, looks_like_html

        if looks_like_html(raw):
            extracted = html_to_markdown(text, max_chars=cap)
            body = str(extracted.get("markdown") or "").strip()
            if extracted.get("js_shell") or len(body) < 80:
                rail = await _rail_page_text(u)
                if rail:
                    return (
                        f"URL: {shown}\nSource: in-app browser (HTTP body was empty or script-only)\n\n"
                        f"{rail[:cap]}"
                    )
            if body:
                title = str(extracted.get("title") or "").strip()
                head = f"URL: {shown}"
                if title:
                    head += f"\nTitle: {title}"
                return f"{head}\n\n{body}"
            rail = await _rail_page_text(u)
            if rail:
                return (
                    f"URL: {shown}\nSource: in-app browser\n\n{rail[:cap]}"
                )
        if len(text) > cap:
            text = text[:cap] + f"\n…[truncated at {cap} chars]"
        return f"URL: {shown}\n\n{text}"

    async def web_search(query: str = "", max_results: float = 5.0) -> str:
        """Search the public web (DuckDuckGo HTML). Same opt-in + SSRF gates as web_fetch.

        Residual: plan mode, skills, and UI labeled web_search while only web_fetch
        was registered — models hit unknown-tool. Now a real SERP digester.
        """
        if not _web_enabled(runtime):
            return _web_disabled_msg("web_search")
        q = (query or "").strip()
        if not q:
            return format_tool_error(
                "query is required",
                code="MISSING_QUERY",
                tool_name="web_search",
                suggestion='web_search(query="site:docs.python.org asyncio gather")',
            )
        if len(q) > 400:
            q = q[:400]
        try:
            n = int(max_results if max_results is not None else 5)
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(10, n))
        # html.duckduckgo.com is public; pin-on-resolve still revalidates DNS.
        search_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": q})
        try:
            _final, raw, charset = _pinned_fetch(
                search_url, max_chars=200_000, timeout=20.0
            )
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_search")
        html = raw.decode(charset or "utf-8", errors="replace")
        rows = parse_ddg_html_results(html, max_results=n)
        if not rows:
            return (
                f"Search: {q}\n\nNo structured results parsed. "
                "Try a simpler query, or web_fetch a known docs URL."
            )
        lines = [f"Search: {q}", f"Results: {len(rows)}", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")
        lines.append(
            "Use web_fetch on promising URLs for full page text. "
            "Private/localhost hosts remain blocked (SSRF)."
        )
        return "\n".join(lines).strip()

    runtime.tool_registry.register_builtin_handler(
        "web_fetch",
        "Fetch an HTTP(S) URL as readable page text (opt-in: web_tools_enabled=true). "
        "HTML is stripped to markdown. Script-only pages fall back to the in-app browser. "
        "Private/localhost hosts are blocked (SSRF).",
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
    runtime.tool_registry.register_builtin_handler(
        "web_search",
        "Search the public web (opt-in: web_tools_enabled=true). "
        "Returns titles, URLs, and snippets; follow up with web_fetch for full pages. "
        "Same SSRF protection as web_fetch (no private/localhost).",
        web_search,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (keywords or site: filter)",
                },
                "max_results": {
                    "type": "number",
                    "description": "Max results to return (default 5, max 10)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )

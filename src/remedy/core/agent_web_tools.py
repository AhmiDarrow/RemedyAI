"""Public web tools (on by default; owner can turn them off).

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
import threading
import time
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from remedy.core.errors import format_tool_error

# Max redirect hops (owner still has full public-web fetch power when enabled).
_MAX_REDIRECTS = 8
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def _remedy_version() -> str:
    try:
        from remedy import __version__

        return str(__version__ or "0").strip() or "0"
    except Exception:
        return "0"


# Say who is calling and where to complain. A bot that names itself can be
# rate-limited or blocked on purpose by a site owner; an anonymous one leaves
# them nothing to aim at but a block-everything rule.
_ROBOTS_AGENT = "RemedyAI-WebFetch"
USER_AGENT = f"{_ROBOTS_AGENT}/{_remedy_version()} (+https://github.com/AhmiDarrow/RemedyAI)"

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
    """On unless the owner turned them off. Missing key = on."""
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if "web_tools_enabled" in cfg:
            return bool(cfg.get("web_tools_enabled"))
    except Exception:
        pass
    if runtime is not None:
        val = getattr(getattr(runtime, "config", None), "web_tools_enabled", None)
        if val is not None:
            return bool(val)
    return True


def web_tools_enabled(runtime: Any = None) -> bool:
    """Public gate — Life research and tools share this opt-in."""
    return _web_enabled(runtime)


# ---------------------------------------------------------------------------
# Politeness: robots.txt and per-host pacing.
#
# robots.txt is not law, and Remedy fetches a page at a time on an owner's
# instruction rather than crawling. It is still the only standing instruction
# a site leaves for automated clients, so the default is to read it and obey
# it — and to leave a gap between hits on the same host. An owner who needs a
# page their own robots rule covers can set ``web_respect_robots = false``.
# ---------------------------------------------------------------------------

_ROBOTS_TTL = 3600.0
_ROBOTS_MAX_HOSTS = 512
_ROBOTS_BYTES = 200_000
# Between two fetches of the same host, unless robots.txt asks for longer.
_DEFAULT_CRAWL_DELAY = 1.0
# A hostile or careless Crawl-delay must not hang a turn: past this we refuse
# the fetch and say why instead of sleeping through the owner's whole turn.
_MAX_POLITE_WAIT = 10.0

_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}
_robots_lock = threading.Lock()
_last_fetch_at: dict[str, float] = {}
_pace_lock = threading.Lock()


def _robots_respected(runtime: Any = None) -> bool:
    """Default true; ``web_respect_robots = false`` in config turns it off."""
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if "web_respect_robots" in cfg:
            return bool(cfg.get("web_respect_robots"))
    except Exception:
        pass
    if runtime is not None:
        val = getattr(getattr(runtime, "config", None), "web_respect_robots", None)
        if val is not None:
            return bool(val)
    return True


def _robots_key(parsed: Any) -> str:
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def _load_robots(origin: str) -> RobotFileParser | None:
    """Fetch and parse ``origin/robots.txt``; None when there is no usable rule.

    Fail-open: an unreachable or unparseable robots.txt means no stated rule,
    which is how a normal browser and most crawlers treat it. A served 200 is
    obeyed exactly.
    """
    now = time.monotonic()
    with _robots_lock:
        hit = _robots_cache.get(origin)
        if hit and now - hit[0] < _ROBOTS_TTL:
            return hit[1]

    parser: RobotFileParser | None = None
    try:
        _final, raw, charset = _pinned_fetch(
            f"{origin}/robots.txt", max_chars=_ROBOTS_BYTES, timeout=8.0
        )
        text = raw.decode(charset or "utf-8", errors="replace")
        parser = RobotFileParser()
        parser.parse(text.splitlines())
    except Exception:
        # No robots.txt, a 4xx, a timeout, or junk — nothing to obey.
        parser = None

    with _robots_lock:
        if len(_robots_cache) >= _ROBOTS_MAX_HOSTS:
            _robots_cache.clear()
        _robots_cache[origin] = (now, parser)
    return parser


def _robots_gate(url: str) -> float:
    """Raise ``ValueError('ROBOTS_BLOCKED …')`` when robots.txt says no.

    Returns the crawl delay this host asks for (seconds, 0 when unstated).
    """
    parsed = urlparse(url)
    origin = _robots_key(parsed)
    parser = _load_robots(origin)
    if parser is None:
        return 0.0
    try:
        allowed = parser.can_fetch(_ROBOTS_AGENT, url)
    except Exception:
        return 0.0
    if not allowed:
        raise ValueError(f"ROBOTS_BLOCKED {parsed.hostname or origin}")
    delay = 0.0
    with suppress(Exception):
        stated = parser.crawl_delay(_ROBOTS_AGENT)
        if stated is not None:
            delay = float(stated)
    return delay


def _pace_host(host: str, crawl_delay: float) -> None:
    """Sleep just long enough that two hits on *host* are not back to back."""
    wait_for = max(_DEFAULT_CRAWL_DELAY, float(crawl_delay or 0.0))
    with _pace_lock:
        last = _last_fetch_at.get(host)
        now = time.monotonic()
        remaining = 0.0 if last is None else wait_for - (now - last)
        if remaining > _MAX_POLITE_WAIT:
            raise ValueError(
                f"ROBOTS_DELAY {host} asks for {wait_for:.0f}s between requests"
            )
        # Claim the slot before releasing the lock so parallel fetches of the
        # same host queue up instead of all reading the same stale timestamp.
        _last_fetch_at[host] = now + max(0.0, remaining)
        if len(_last_fetch_at) > _ROBOTS_MAX_HOSTS:
            for stale in [k for k, v in _last_fetch_at.items() if now - v > 600][:256]:
                _last_fetch_at.pop(stale, None)
    if remaining > 0:
        time.sleep(remaining)


def polite_fetch(
    url: str,
    *,
    max_chars: int,
    timeout: float = 25.0,
    respect_robots: bool | None = None,
    runtime: Any = None,
) -> tuple[str, bytes, str]:
    """``_pinned_fetch`` with robots.txt honoured and per-host pacing applied.

    ``_pinned_fetch`` stays pure transport (SSRF, redirects, caps); policy
    lives here so the two can be reasoned about — and tested — apart.
    """
    if respect_robots is None:
        respect_robots = _robots_respected(runtime)
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    delay = _robots_gate(url) if respect_robots else 0.0
    if host:
        _pace_host(host, delay)
    final_url, raw, charset = _pinned_fetch(url, max_chars=max_chars, timeout=timeout)
    # A redirect can land on a host whose robots.txt was never consulted.
    # The body is already in hand, but a disallowed page is not ours to read.
    if respect_robots:
        final_host = (urlparse(final_url).hostname or "").lower()
        if final_host and final_host != host:
            _robots_gate(final_url)
    return final_url, raw, charset


# ---------------------------------------------------------------------------
# Search backends.
#
# There is no keyless, terms-clean, general web-result API to point at: the
# ones with an index behind them (Brave, Mojeek, Marginalia) want a key, and
# the keyless ones aggregate by scraping somebody else. So the order is:
#
#   1. an instance the owner runs themselves (SearXNG) — no third party in it
#   2. reading DuckDuckGo's no-JavaScript results page — allowed by their
#      robots.txt, self-identified and paced, but still automated use of a
#      service that can throttle or refuse it, so the owner says yes once
#
# docs/WEB_ETIQUETTE.md carries the long version.
# ---------------------------------------------------------------------------

_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


class SearchConsentError(RuntimeError):
    """Raised when the scraping fallback is the only route and is un-acked."""


def _cfg(key: str, runtime: Any = None) -> Any:
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if key in cfg:
            return cfg.get(key)
    except Exception:
        pass
    if runtime is not None:
        return getattr(getattr(runtime, "config", None), key, None)
    return None


def _searxng_base(runtime: Any = None) -> str:
    """Owner's own metasearch instance, when they run one."""
    val = _cfg("web_search_url", runtime)
    base = str(val or "").strip().rstrip("/")
    return base if base.startswith(("http://", "https://")) else ""


def _scraping_acked(runtime: Any = None) -> bool:
    return bool(_cfg("web_search_scraping_ack", runtime))


SCRAPING_ACK_PROMPT = (
    "Web search is on. Remedy prefers the local OpenSERP instance she "
    "downloads on first run, then DuckDuckGo's no-JavaScript results page. "
    "You can point her at your own SearXNG with "
    'update_settings(web_search_url="http://…") or turn the tools off with '
    "update_settings(web_tools_enabled=false)."
)


def _searxng_rows(base: str, q: str, n: int, timeout: float) -> list[dict[str, str]]:
    """Query a SearXNG instance's JSON API.

    The instance admin must have ``json`` in ``search.formats`` — a stock
    install answers 403 to ``format=json`` and we say so rather than guessing.
    """
    import json as _json

    url = f"{base}/search?" + urlencode({"q": q, "format": "json"})
    host = (urlparse(base).hostname or "").lower()
    # A self-hosted instance usually lives on loopback or the LAN, which the
    # SSRF guard exists to refuse. Opening that hole silently would undo the
    # guard for anything that can write config, so the owner opens it by hand.
    if _host_is_blocked(host):
        if not bool(_cfg("web_search_url_allow_private")):
            raise ValueError(
                "SEARCH_PRIVATE_HOST "
                f"{host} is a private/loopback address; set "
                "web_search_url_allow_private=true to allow your own instance"
            )
        raw = _direct_fetch(url, timeout=timeout)
        text = raw.decode("utf-8", errors="replace")
    else:
        _final, body, charset = polite_fetch(
            url, max_chars=400_000, timeout=timeout, respect_robots=False
        )
        text = body.decode(charset or "utf-8", errors="replace")

    try:
        data = _json.loads(text)
    except ValueError as exc:
        # A stock SearXNG serves HTML and answers 403 to format=json until the
        # admin puts json in search.formats. Say that instead of "bad JSON".
        raise ValueError(
            f"SEARCH_BACKEND {base} did not return JSON — add 'json' to "
            f"search.formats in its settings.yml ({exc})"
        ) from exc
    rows: list[dict[str, str]] = []
    for item in (data.get("results") or [])[: max(1, n)]:
        target = str(item.get("url") or "").strip()
        if not target.startswith(("http://", "https://")):
            continue
        rows.append(
            {
                "title": _strip_tags(str(item.get("title") or "")) or target,
                "url": target,
                "snippet": _strip_tags(str(item.get("content") or ""))[:300],
            }
        )
    return rows


def _direct_fetch(url: str, *, timeout: float) -> bytes:
    """Plain fetch with no SSRF pin — only for an owner-declared private host."""
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return bytes(resp.read(2_000_000))


def _ddg_rows(q: str, n: int, timeout: float) -> list[dict[str, str]]:
    search_url = _DDG_HTML_ENDPOINT + "?" + urlencode({"q": q})
    # Paced like any other host, but not robots-gated: their robots.txt says
    # Allow: / for every agent, so there is no stated rule to consult here.
    _final, raw, charset = polite_fetch(
        search_url, max_chars=200_000, timeout=timeout, respect_robots=False
    )
    html = raw.decode(charset or "utf-8", errors="replace")
    return parse_ddg_html_results(html, max_results=n)


def _openserp_rows(q: str, n: int, timeout: float) -> list[dict[str, str]]:
    """Query the managed loopback OpenSERP. Raises on transport failure."""
    import json as _json

    from remedy.runtime.web_search_host import base_url, is_healthy

    if not is_healthy():
        raise RuntimeError("openserp not healthy")
    base = base_url().rstrip("/")
    engines = ("duckduckgo", "ecosia")
    last_err: Exception | None = None
    for engine in engines:
        url = f"{base}/{engine}/search?" + urlencode(
            {"text": q, "limit": str(max(1, n))}
        )
        try:
            raw = _direct_fetch(url, timeout=timeout)
            data = _json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            last_err = exc
            continue
        rows: list[dict[str, str]] = []
        for item in data.get("results") or []:
            if str(item.get("type") or "organic") not in ("organic", ""):
                continue
            target = str(item.get("url") or "").strip()
            if not target.startswith(("http://", "https://")):
                continue
            rows.append(
                {
                    "title": _strip_tags(str(item.get("title") or "")) or target,
                    "url": target,
                    "snippet": _strip_tags(str(item.get("snippet") or ""))[:300],
                }
            )
            if len(rows) >= n:
                break
        if rows:
            return rows
    if last_err is not None:
        raise last_err
    return []


def run_search(
    q: str, *, max_results: int, timeout: float, runtime: Any = None
) -> tuple[list[dict[str, str]], str]:
    """Search via the best available backend. Returns (rows, backend_name).

    Order: owner SearXNG if set, then the managed OpenSERP on loopback, then
    DuckDuckGo HTML. The installer ToS already covers automated access, so the
    HTML fallback no longer waits for a second ack.
    """
    base = _searxng_base(runtime)
    if base:
        return _searxng_rows(base, q, max_results, timeout), "your search instance"
    try:
        rows = _openserp_rows(q, max_results, timeout)
        if rows:
            return rows, "OpenSERP (local)"
    except Exception:
        pass
    return _ddg_rows(q, max_results, timeout), "DuckDuckGo HTML"


def search_public_web(
    query: str,
    *,
    max_results: int = 3,
    timeout: float = 12.0,
) -> list[dict[str, str]]:
    """Sync background search. Empty if web tools are off or the net fails.

    Never raises — a down backend reads as no results here.
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
    try:
        rows, _backend = run_search(q, max_results=n, timeout=timeout)
        return rows
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


#: A redirect body is never used. Read a little so the socket closes cleanly,
#: and never more.
_REDIRECT_DRAIN_BYTES = 8192


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
        aborted = False
        try:
            from remedy.core.turn_context import is_turn_aborted

            aborted = bool(is_turn_aborted())
        except Exception:
            aborted = False
        if aborted:
            raise ValueError("ABORTED")
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
            "User-Agent": USER_AGENT,
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
                # Drain, but bounded. Every other read here is capped; this one
                # was not, and a hostile server can answer a 302 with a
                # multi-gigabyte body — read whole, into memory, before the
                # redirect is even looked at. Nothing here needs the body.
                _read_capped(resp, _REDIRECT_DRAIN_BYTES)
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


def _rail_url_matches(requested: str, observed: str) -> bool:
    """True when the rail is actually on the URL we asked to fetch.

    Live 2026-08-27: navigate returned pending_load, then PAGE_TEXT read the
    owner's current Reddit tab and web_fetch attributed it to PyPI.
    """
    from urllib.parse import urlparse

    req = (requested or "").strip()
    obs = (observed or "").strip()
    if not req or not obs:
        return False
    try:
        a, b = urlparse(req), urlparse(obs)
    except Exception:
        return False
    host_a = (a.hostname or "").lower().removeprefix("www.")
    host_b = (b.hostname or "").lower().removeprefix("www.")
    if not host_a or host_a != host_b:
        return False
    path_a = (a.path or "/").rstrip("/") or "/"
    path_b = (b.path or "/").rstrip("/") or "/"
    return path_a == path_b


async def _rail_page_text(url: str) -> str:
    """If the in-app browser can open *url*, return visible page text.

    Used when HTTP HTML is an empty shell (JS apps / bot walls). Failures
    return "" — web_fetch still has the HTTP extract.
    """
    import asyncio

    def _as_dict(raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                import json as _json

                obj = _json.loads(raw)
            except Exception:
                return {}
            return obj if isinstance(obj, dict) else {}
        return {}

    def _run() -> str:
        try:
            from remedy.core.computer.executor import get_computer_executor
            from remedy.core.computer.types import ComputerAction

            ex = get_computer_executor()
            nav = _as_dict(ex.run(ComputerAction.NAVIGATE, url=url, target="browser"))
            if not nav or nav.get("ok") is False:
                return ""
            # Fire-and-forget navigate is not a loaded page — wait once, then
            # only keep the text if the rail URL matches the fetch target.
            if nav.get("pending_load") or nav.get("observed") is False:
                ex.run(ComputerAction.WAIT, seconds=1.2, target="browser")
            page = _as_dict(ex.run(ComputerAction.PAGE_TEXT, target="browser"))
            if not page:
                return ""
            observed = str(page.get("url") or nav.get("url") or "").strip()
            if not _rail_url_matches(url, observed):
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
    """Register web_fetch + web_search (on unless the owner turned them off)."""

    def _web_disabled_msg(tool_name: str) -> str:
        return format_tool_error(
            "Web tools are off. The owner turned them off — ask before turning "
            f"them back on, then retry {tool_name}.",
            code="WEB_DISABLED",
            tool_name=tool_name,
            suggestion=(
                "If the owner wants them on: update_settings(web_tools_enabled=true)."
            ),
        )

    def _map_fetch_error(e: BaseException, *, tool_name: str) -> str:
        if isinstance(e, ValueError):
            msg = str(e)
            if msg == "ABORTED":
                return format_tool_error(
                    "Aborted by user",
                    code="ABORTED",
                    tool_name=tool_name,
                )
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
            if msg.startswith("ROBOTS_BLOCKED"):
                host = msg.split(" ", 1)[-1]
                return format_tool_error(
                    f"Skipped: {host} disallows automated clients on this path in robots.txt.",
                    code="ROBOTS_BLOCKED",
                    tool_name=tool_name,
                    suggestion=(
                        "Tell the owner the site asks bots not to read that page. They can "
                        "open it themselves, or set web_respect_robots=false to override."
                    ),
                )
            if msg.startswith("ROBOTS_DELAY"):
                return format_tool_error(
                    f"Skipped: {msg.split(' ', 1)[-1]}, which is longer than a turn should wait.",
                    code="ROBOTS_DELAY",
                    tool_name=tool_name,
                    suggestion="Fetch a different host now and come back to this one later.",
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
            final_url, raw, charset = polite_fetch(
                u, max_chars=max(cap * 4, 200_000), timeout=25.0, runtime=runtime
            )
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_fetch")

        shown = final_url if final_url != u else u
        text = raw.decode(charset or "utf-8", errors="replace")
        with suppress(Exception):
            from remedy.core.turn_context import current_turn_id, turn_session_id
            from remedy.memory.provenance import ingest_web_text

            ingest_web_text(
                text[:800],
                session_id=str(turn_session_id(runtime) or ""),
                turn_id=str(current_turn_id() or ""),
            )
        from remedy.core.html_extract import html_to_markdown, looks_like_html

        if looks_like_html(raw):
            extracted = html_to_markdown(text, max_chars=cap)
            body = str(extracted.get("markdown") or "").strip()
            # A thin extract is worth a second look, but the browser only
            # wins when it actually returns more than the HTTP extract did
            # — a short page is not the same thing as an empty shell.
            shell = bool(extracted.get("js_shell")) or not body
            if shell or len(body) < 80:
                rail = await _rail_page_text(u)
                if rail and (shell or len(rail) > len(body)):
                    why = (
                        "HTTP body was empty or script-only"
                        if shell
                        else "HTTP extract was thin"
                    )
                    return (
                        f"URL: {shown}\nSource: in-app browser ({why})\n\n"
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
        """Search the public web. Local OpenSERP if ready, else DuckDuckGo HTML."""
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
        try:
            rows, backend = run_search(q, max_results=n, timeout=20.0, runtime=runtime)
        except SearchConsentError as consent:
            return format_tool_error(
                str(consent),
                code="SEARCH_CONSENT_REQUIRED",
                tool_name="web_search",
            )
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_search")
        if not rows:
            return (
                f"Search: {q}\n\nNo structured results parsed. "
                "Try a simpler query, or web_fetch a known docs URL."
            )
        lines = [f"Search: {q}", f"Source: {backend}", f"Results: {len(rows)}", ""]
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
        "Fetch an HTTP(S) URL as readable page text. "
        "HTML is stripped to markdown. Script-only pages fall back to the in-app browser. "
        "Private/localhost hosts are blocked (SSRF). Off only if the owner disabled web tools.",
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
        "Search the public web. Local OpenSERP (downloaded on first run) or "
        "DuckDuckGo HTML. Returns titles, URLs, and snippets; follow up with "
        "web_fetch for full pages. Private/localhost hosts stay blocked (SSRF).",
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

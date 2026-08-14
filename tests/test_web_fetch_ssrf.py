"""web_fetch SSRF guard + pin-on-resolve helpers."""

from __future__ import annotations

import ipaddress
from unittest.mock import patch

import pytest

from remedy.core.agent_web_tools import (
    _host_is_blocked,
    _ip_is_blocked,
    _prefer_connect_ip,
    _resolve_public_ips,
)


def test_blocks_localhost_and_private():
    assert _host_is_blocked("localhost")
    assert _host_is_blocked("127.0.0.1")
    assert _host_is_blocked("10.0.0.1")
    assert _host_is_blocked("192.168.1.1")
    assert _host_is_blocked("169.254.169.254")
    assert _host_is_blocked("metadata.google.internal")
    assert _host_is_blocked("metadata.nicob.net")
    assert _host_is_blocked("instance-data")
    assert _host_is_blocked("1.2.3.4.nip.io")


def test_allows_public_literal_ip():
    assert not _host_is_blocked("8.8.8.8")
    assert not _host_is_blocked("1.1.1.1")


def test_ip_is_blocked_helpers():
    assert _ip_is_blocked(ipaddress.ip_address("127.0.0.1"))
    assert _ip_is_blocked(ipaddress.ip_address("10.0.0.1"))
    assert not _ip_is_blocked(ipaddress.ip_address("8.8.8.8"))


def test_resolve_public_literal():
    assert _resolve_public_ips("1.1.1.1") == ["1.1.1.1"]
    assert _resolve_public_ips("127.0.0.1") == []


def test_resolve_fail_closed_on_private_in_answers():
    """If DNS returns any private address, treat host as blocked (empty list)."""

    # Simulate dual-stack rebinding face: public + private
    fake = [
        (0, 0, 0, "", ("8.8.8.8", 0)),
        (0, 0, 0, "", ("10.0.0.1", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        assert _resolve_public_ips("evil.example") == []


def test_resolve_public_only():
    fake = [
        (0, 0, 0, "", ("8.8.8.8", 0)),
        (0, 0, 0, "", ("1.1.1.1", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        ips = _resolve_public_ips("good.example")
        assert "8.8.8.8" in ips
        assert "1.1.1.1" in ips


def test_prefer_ipv4():
    assert _prefer_connect_ip(["2001:4860:4860::8888", "8.8.8.8"]) == "8.8.8.8"
    assert _prefer_connect_ip(["2001:4860:4860::8888"]) == "2001:4860:4860::8888"


def test_host_blocked_when_dns_empty():
    with patch("socket.getaddrinfo", side_effect=OSError("nxdomain")):
        assert _host_is_blocked("no-such-host.invalid") is True


def test_blocks_cgnat_and_non_global_ips():
    """CGNAT 100.64/10 is not is_private on some Pythons — still blocked."""
    assert _ip_is_blocked(ipaddress.ip_address("100.64.0.1"))
    assert _host_is_blocked("100.64.1.1")
    assert _resolve_public_ips("100.64.0.1") == []


def test_pinned_fetch_blocks_url_userinfo():
    """user:pass@host must never be fetched (credential leak / SSRF)."""
    from remedy.core.agent_web_tools import _pinned_fetch

    with pytest.raises(ValueError, match="USERINFO"):
        _pinned_fetch("https://user:secret@example.com/path", max_chars=1000)
    with pytest.raises(ValueError, match="USERINFO"):
        _pinned_fetch("http://alice@8.8.8.8/", max_chars=1000)


def test_pinned_fetch_revalidates_redirect_private_and_userinfo():
    """Redirect hops must re-run SSRF + userinfo checks (no open-redirect pivot).

    Gap: unit coverage previously only exercised first-hop userinfo / private
    hosts. A 302 Location to loopback or user:pass@host must fail closed before
    the next connect.
    """
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import _pinned_fetch

    def _fake_conn_factory(location: str):
        class _Conn:
            def __init__(self, *a, **k):
                pass

            def request(self, *a, **k):
                pass

            def getresponse(self):
                resp = MagicMock()
                resp.status = 302
                resp.reason = "Found"
                resp.headers = {}
                resp.getheader = lambda name, default=None: (
                    location if str(name).lower() == "location" else default
                )
                resp.read = lambda *a, **k: b""
                return resp

            def close(self):
                pass

        return _Conn

    # First hop is a public literal so host/DNS checks pass; pin uses that IP.
    with patch(
        "remedy.core.agent_web_tools.http.client.HTTPConnection",
        _fake_conn_factory("http://127.0.0.1/metadata"),
    ):
        with pytest.raises(ValueError, match="SSRF_BLOCKED_REDIRECT"):
            _pinned_fetch("http://1.1.1.1/start", max_chars=1000)

    with patch(
        "remedy.core.agent_web_tools.http.client.HTTPConnection",
        _fake_conn_factory("http://user:pass@8.8.8.8/leak"),
    ):
        with pytest.raises(ValueError, match="USERINFO"):
            _pinned_fetch("http://1.1.1.1/start", max_chars=1000)

    with patch(
        "remedy.core.agent_web_tools.http.client.HTTPConnection",
        _fake_conn_factory("http://169.254.169.254/latest/meta-data/"),
    ):
        with pytest.raises(ValueError, match="SSRF_BLOCKED_REDIRECT"):
            _pinned_fetch("http://1.1.1.1/start", max_chars=1000)


def test_blocks_ipv6_ula_link_local_loopback_and_mapped():
    """IPv6 ULA/doc/link-local/loopback/multicast and IPv4-mapped loopback stay blocked.

    Audit gap (S-SSRF): CGNAT covered elsewhere; these v6 faces must not slip past
    ``not is_global`` / private / multicast helpers into pin-on-resolve fetch.
    """
    blocked = [
        "::1",  # loopback
        "fe80::1",  # link-local
        "fc00::1",  # ULA fc00::/7
        "fd12:3456:789a:1::1",  # ULA fd00::/8 face
        "2001:db8::1",  # documentation
        "ff02::1",  # multicast (is_global may be True on some Pythons)
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::",  # unspecified
    ]
    for addr in blocked:
        ip = ipaddress.ip_address(addr)
        assert _ip_is_blocked(ip), f"expected blocked: {addr}"
        assert _host_is_blocked(addr), f"host blocked: {addr}"
        assert _resolve_public_ips(addr) == [], f"resolve empty: {addr}"

    # Globally routable v6 still allowed (Google DNS)
    public_v6 = "2001:4860:4860::8888"
    assert not _ip_is_blocked(ipaddress.ip_address(public_v6))
    assert not _host_is_blocked(public_v6)
    assert _resolve_public_ips(public_v6) == [public_v6]

    # Dual-stack rebinding: public v4 + ULA v6 → fail closed
    fake = [
        (0, 0, 0, "", ("8.8.8.8", 0)),
        (0, 0, 0, "", ("fd00::1", 0, 0, 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        assert _resolve_public_ips("evil-dual.example") == []


def test_parse_ddg_html_results():
    from remedy.core.agent_web_tools import parse_ddg_html_results

    html = """
    <div class="result">
      <a class="result__a" href="https://html.duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html">
        asyncio — Asynchronous I&amp;O
      </a>
      <a class="result__snippet">Concurrent code with async/await.</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.com/guide">Example Guide</a>
      <a class="result__snippet">A short guide.</a>
    </div>
    """
    rows = parse_ddg_html_results(html, max_results=5)
    assert len(rows) >= 2
    assert rows[0]["url"] == "https://docs.python.org/3/library/asyncio.html"
    assert "asyncio" in rows[0]["title"].lower()
    assert rows[1]["url"] == "https://example.com/guide"
    assert "guide" in rows[1]["title"].lower()


def test_search_public_web_disabled(monkeypatch):
    from remedy.core import agent_web_tools as w

    monkeypatch.setattr(w, "_web_enabled", lambda runtime=None: False)
    assert w.search_public_web("spanish beginner") == []


@pytest.mark.asyncio
async def test_web_search_disabled_soft_error(monkeypatch):
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import register_web_tools
    from remedy.core.react_policy import tool_content_is_error
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(
        "remedy.core.agent_web_tools._web_enabled", lambda runtime: False
    )
    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    register_web_tools(runtime)
    assert runtime.tool_registry.get("web_search") is not None
    out = await runtime.tool_registry.execute("web_search", query="asyncio gather")
    assert tool_content_is_error(out) or "WEB_DISABLED" in out or "disabled" in out.lower()


@pytest.mark.asyncio
async def test_web_search_parses_pinned_html(monkeypatch):
    """Enabled search uses pin-on-resolve fetch; parser formats results (no real net)."""
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import register_web_tools
    from remedy.skills.tool_registry import ToolRegistry

    html = (
        b'<a class="result__a" href="https://example.com/a">Alpha Title</a>'
        b'<a class="result__snippet">Alpha snip</a>'
        b'<a class="result__a" href="https://example.com/b">Beta Title</a>'
        b'<a class="result__snippet">Beta snip</a>'
    )

    def fake_pinned(url, *, max_chars=50_000, timeout=25.0):
        assert "duckduckgo.com" in url
        assert "q=" in url
        return url, html, "utf-8"

    monkeypatch.setattr(
        "remedy.core.agent_web_tools._pinned_fetch", fake_pinned
    )
    monkeypatch.setattr(
        "remedy.core.agent_web_tools._web_enabled", lambda runtime: True
    )

    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    register_web_tools(runtime)
    out = await runtime.tool_registry.execute(
        "web_search", query="alpha beta", max_results=5
    )
    assert "Alpha Title" in out
    assert "https://example.com/a" in out
    assert "Beta Title" in out
    assert "web_fetch" in out.lower()

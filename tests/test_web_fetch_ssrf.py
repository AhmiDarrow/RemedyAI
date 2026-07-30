"""web_fetch SSRF guard + pin-on-resolve helpers."""

from __future__ import annotations

import ipaddress
from unittest.mock import patch

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
    import pytest

    from remedy.core.agent_web_tools import _pinned_fetch

    with pytest.raises(ValueError, match="USERINFO"):
        _pinned_fetch("https://user:secret@example.com/path", max_chars=1000)
    with pytest.raises(ValueError, match="USERINFO"):
        _pinned_fetch("http://alice@8.8.8.8/", max_chars=1000)


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

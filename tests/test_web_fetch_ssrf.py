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

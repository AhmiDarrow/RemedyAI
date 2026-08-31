"""Chosen-IPv4 bind: wildcard family refused, unicast IPv4 accepted."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from remedy.connect.bind import (
    WILDCARD,
    assert_chosen_bind,
    default_route_ipv4,
    is_chosen_ipv4,
    is_loopback_ipv4,
    is_wildcard_bind,
    list_candidate_ipv4,
    pick_default_ipv4,
    prefer_lan_ipv4,
    reachable_lan_host,
)


@pytest.mark.parametrize("host", ["0.0.0.0", "*", "::", "[::]"])
def test_wildcard_family_refused(host: str) -> None:
    assert host in WILDCARD or is_wildcard_bind(host)
    assert is_wildcard_bind(host)
    with pytest.raises(ValueError):
        assert_chosen_bind(host)
    assert is_chosen_ipv4(host) is False


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "*",
        "::",
        "[::]",
        "0:0:0:0:0:0:0:0",
        " :: ",
        " 0.0.0.0 ",
        "[::0]",
        "::0",
        "0.0.0.0/0",
    ],
)
def test_wildcard_family_extended(host: str) -> None:
    if "/" in host:
        # CIDR is not a bind host; not a chosen unicast IPv4 either.
        assert is_chosen_ipv4(host) is False
        return
    assert is_wildcard_bind(host) is True
    with pytest.raises(ValueError):
        assert_chosen_bind(host)


@pytest.mark.parametrize("host", ["", "   ", None])
def test_empty_bind_refused(host: str | None) -> None:
    text = "" if host is None else host
    with pytest.raises(ValueError):
        assert_chosen_bind(text)
    assert is_chosen_ipv4(text) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5"])
def test_loopback_and_lan_accepted_as_chosen_ipv4(host: str) -> None:
    assert is_chosen_ipv4(host) is True
    assert assert_chosen_bind(host) == host
    assert is_wildcard_bind(host) is False


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.10",
        "172.16.0.1",
        "8.8.8.8",
        "127.0.0.2",
        " 10.0.0.5 ",
    ],
)
def test_unicast_ipv4_family_accepted(host: str) -> None:
    assert is_chosen_ipv4(host) is True
    assert assert_chosen_bind(host) == host.strip()


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "example.com",
        "host.local",
        "::1",
        "[::1]",
        "255.255.255.255",
        "224.0.0.1",
        "fe80::1",
        "not-an-ip",
        "10.0.0",
        "10.0.0.5.1",
    ],
)
def test_hostname_multicast_broadcast_not_chosen_ipv4(host: str) -> None:
    assert is_chosen_ipv4(host) is False


def test_assert_chosen_bind_strips_but_allows_hostname() -> None:
    assert assert_chosen_bind("  office-pc  ") == "office-pc"


def test_list_candidate_ipv4_excludes_wildcard() -> None:
    addrs = list_candidate_ipv4()
    assert isinstance(addrs, list)
    assert "0.0.0.0" not in addrs
    assert "*" not in addrs
    for ip in addrs:
        assert is_chosen_ipv4(ip)
        assert not is_wildcard_bind(ip)
    assert "127.0.0.1" in addrs
    lan = [ip for ip in addrs if not is_loopback_ipv4(ip)]
    if lan:
        assert not is_loopback_ipv4(addrs[0])
        # Route-first order: the first candidate is the same reachable LAN
        # host the default picker chooses (no-args uses the same route-first
        # path, so a phone-visible address — not a WSL/Docker NAT — wins).
        assert addrs[0] == pick_default_ipv4()


def test_list_candidate_ipv4_filters_wildcard_from_os(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ex(_name: str) -> tuple[str, list[str], list[str]]:
        return "testhost", [], ["0.0.0.0", "10.0.0.5", "127.0.0.1"]

    def fake_addrinfo(*_a: object, **_k: object) -> list[tuple[object, ...]]:
        return [
            (None, None, None, None, ("0.0.0.0", 0)),
            (None, None, None, None, ("192.168.1.20", 0)),
        ]

    monkeypatch.setattr("socket.gethostname", lambda: "testhost")
    monkeypatch.setattr("socket.gethostbyname_ex", fake_ex)
    monkeypatch.setattr("socket.getaddrinfo", fake_addrinfo)
    with patch("socket.socket") as sock_cls:
        sock_cls.side_effect = OSError("no udp")
        addrs = list_candidate_ipv4()
    assert "0.0.0.0" not in addrs
    assert "10.0.0.5" in addrs
    assert "192.168.1.20" in addrs
    assert "127.0.0.1" in addrs
    assert all(is_chosen_ipv4(ip) for ip in addrs)
    lan = [ip for ip in addrs if not is_loopback_ipv4(ip)]
    if lan:
        assert addrs.index(lan[0]) < addrs.index("127.0.0.1")


def test_prefer_lan_ipv4_puts_loopback_last() -> None:
    assert prefer_lan_ipv4(["127.0.0.1", "10.0.0.5", "192.168.1.20"]) == [
        "10.0.0.5",
        "192.168.1.20",
        "127.0.0.1",
    ]
    assert prefer_lan_ipv4(["127.0.0.1"]) == ["127.0.0.1"]
    assert prefer_lan_ipv4(["0.0.0.0", "10.1.2.3"]) == ["10.1.2.3"]
    # Sorted order would put 127.0.0.1 first; LAN unicast must win.
    assert prefer_lan_ipv4(["127.0.0.1", "10.0.0.5"])[0] == "10.0.0.5"


def test_pick_default_ipv4_skips_loopback_when_lan_exists() -> None:
    assert pick_default_ipv4(["127.0.0.1", "10.0.0.5", "192.168.1.20"]) == "10.0.0.5"
    assert pick_default_ipv4(["127.0.0.1"]) == "127.0.0.1"
    assert pick_default_ipv4(["127.0.0.2", "127.0.0.1"]) == "127.0.0.1"
    assert pick_default_ipv4(["0.0.0.0", "*", "10.0.0.8"]) == "10.0.0.8"
    assert pick_default_ipv4([]) == ""
    assert pick_default_ipv4(["127.0.0.1", "10.0.0.5"]) == prefer_lan_ipv4(
        ["127.0.0.1", "10.0.0.5"]
    )[0]


def test_prefer_lan_ipv4_preferred_default_route_wins() -> None:
    """The default-route source beats higher-octet virtual NAT addrs.

    Lexicographic AND numeric sorts would both put 172.x before 192.x;
    ``preferred`` is what makes the real NIC win for phone pairing.
    """
    rows = prefer_lan_ipv4(
        ["172.16.0.1", "192.168.0.4", "127.0.0.1"],
        preferred=("192.168.0.4",),
    )
    assert rows[0] == "192.168.0.4"
    assert rows[1] == "172.16.0.1"
    assert rows[-1] == "127.0.0.1"
    # Preferred that is not in the candidate set is simply ignored.
    assert prefer_lan_ipv4(["10.0.0.5"], preferred=("192.168.0.4",)) == ["10.0.0.5"]


def test_default_route_ipv4_empty_when_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("socket.socket") as sock_cls:
        sock_cls.side_effect = OSError("no udp")
        assert default_route_ipv4() == ""


def test_reachable_lan_host_demotes_virtual_nat(monkeypatch: pytest.MonkeyPatch) -> None:
    """WSL/Docker/Hyper-V NAT and loopback are demoted to the real route."""
    monkeypatch.setattr(
        "remedy.connect.bind.default_route_ipv4", lambda: "192.168.0.4"
    )
    # WSL vEthernet style address -> real LAN route wins.
    assert reachable_lan_host("172.16.0.1") == "192.168.0.4"
    # Loopback -> real LAN route wins.
    assert reachable_lan_host("127.0.0.1") == "192.168.0.4"
    # Empty config -> default route.
    assert reachable_lan_host("") == "192.168.0.4"
    # Explicit non-virtual LAN picks are kept.
    assert reachable_lan_host("10.1.2.3") == "10.1.2.3"
    assert reachable_lan_host("192.168.5.9") == "192.168.5.9"


def test_reachable_lan_host_keeps_config_without_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.default_route_ipv4", lambda: "")
    assert reachable_lan_host("172.16.0.1") == "172.16.0.1"
    assert reachable_lan_host("127.0.0.1") == "127.0.0.1"
    assert reachable_lan_host("10.1.2.3") == "10.1.2.3"

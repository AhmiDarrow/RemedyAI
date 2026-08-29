"""Chosen-IPv4 bind helpers. Wildcard binds (0.0.0.0 / :: / *) are refused."""

from __future__ import annotations

import ipaddress
import socket

WILDCARD = frozenset({"0.0.0.0", "::", "[::]", "*", "0:0:0:0:0:0:0:0"})
_LIMITED_BROADCAST = ipaddress.IPv4Address("255.255.255.255")


def is_wildcard_bind(host: str) -> bool:
    text = (host or "").strip()
    if not text:
        return False
    if text in WILDCARD:
        return True
    inner = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    if inner in WILDCARD:
        return True
    try:
        return ipaddress.ip_address(inner).is_unspecified
    except ValueError:
        return False


def assert_chosen_bind(host: str) -> str:
    """Raise ValueError if wildcard or empty. Return stripped host."""
    text = (host or "").strip()
    if not text:
        raise ValueError("bind host is empty")
    if is_wildcard_bind(text):
        raise ValueError("wildcard bind is not allowed")
    return text


def is_chosen_ipv4(host: str) -> bool:
    """True for a unicast IPv4 (including 127.0.0.1). False for wildcard/hostname/empty."""
    text = (host or "").strip()
    if not text or is_wildcard_bind(text):
        return False
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return not (ip.is_unspecified or ip.is_multicast or ip == _LIMITED_BROADCAST)


def is_loopback_ipv4(host: str) -> bool:
    text = (host or "").strip()
    try:
        return bool(ipaddress.IPv4Address(text).is_loopback)
    except ValueError:
        return False


def prefer_lan_ipv4(addrs: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """LAN unicast first, loopback last. Drops non-chosen addresses."""
    lan: list[str] = []
    loop: list[str] = []
    seen: set[str] = set()
    for raw in addrs:
        ip = str(raw or "").strip()
        if not ip or ip in seen or not is_chosen_ipv4(ip):
            continue
        seen.add(ip)
        if is_loopback_ipv4(ip):
            loop.append(ip)
        else:
            lan.append(ip)
    return sorted(lan) + sorted(loop)


def list_candidate_ipv4() -> list[str]:
    """Host IPv4s excluding 0.0.0.0. LAN unicast first; 127.0.0.1 last."""
    found: set[str] = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        _, _, addrs = socket.gethostbyname_ex(hostname)
        found.update(ip for ip in addrs if is_chosen_ipv4(ip))
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if is_chosen_ipv4(str(ip)):
                found.add(str(ip))
    except OSError:
        pass
    for dest in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(dest)
                ip = sock.getsockname()[0]
                if is_chosen_ipv4(ip):
                    found.add(ip)
        except OSError:
            continue
    return prefer_lan_ipv4(found)


def is_global_ipv6(host: str) -> bool:
    """True for a global unicast IPv6 (not link-local, ULA, loopback, or wildcard)."""
    text = (host or "").strip().strip("[]").split("%", 1)[0]
    if not text or is_wildcard_bind(text):
        return False
    try:
        ip = ipaddress.IPv6Address(text)
    except ValueError:
        return False
    return bool(ip.is_global)


def list_candidate_ipv6() -> list[str]:
    """Global unicast IPv6 addresses on this host, if any."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            raw = str(info[4][0]).split("%", 1)[0]
            if is_global_ipv6(raw):
                found.add(raw)
    except OSError:
        pass
    return sorted(found)

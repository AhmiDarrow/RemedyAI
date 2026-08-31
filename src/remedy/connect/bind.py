"""Chosen-IPv4 bind helpers. Wildcard binds (0.0.0.0 / :: / *) are refused."""

from __future__ import annotations

import ipaddress
import socket

WILDCARD = frozenset({"0.0.0.0", "::", "[::]", "*", "0:0:0:0:0:0:0:0"})
_LIMITED_BROADCAST = ipaddress.IPv4Address("255.255.255.255")

# Virtual NAT ranges a phone on the LAN can never reach (WSL2, Docker,
# Hyper-V internal, CGNAT, link-local). Used only to demote an address when a
# real default-route source exists — never to reject an explicit user pick.
_VIRTUAL_NAT_NETS = (
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
)


def _ipv4_key(ip: str) -> tuple[int, int, int, int]:
    """Numeric sort key so 10.x < 172.x < 192.x like humans expect."""
    try:
        return tuple(int(part) for part in ip.split("."))  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0, 0)


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


def prefer_lan_ipv4(
    addrs: list[str] | tuple[str, ...] | set[str],
    preferred: list[str] | tuple[str, ...] | set[str] = (),
) -> list[str]:
    """LAN unicast first, loopback last. Drops non-chosen addresses.

    ``preferred`` addresses (the default-route source) sort before all other
    LAN unicast — the real NIC must beat virtual NAT adapters even when its
    octets sort higher. Ordering is numeric, never lexicographic.
    """
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
    pre = [str(p).strip() for p in preferred if str(p or "").strip() in seen]
    rest = [ip for ip in lan if ip not in pre]
    return sorted(pre, key=_ipv4_key) + sorted(rest, key=_ipv4_key) + sorted(loop, key=_ipv4_key)


def pick_default_ipv4(addrs: list[str] | tuple[str, ...] | set[str] | None = None) -> str:
    """First LAN unicast (default-route source wins). Loopback only when alone.

    No args: uses the route-first order from :func:`list_candidate_ipv4`.
    Explicit list: numeric sort still applies (10.x < 172.x < 192.x).
    """
    rows = (
        list_candidate_ipv4()
        if addrs is None
        else prefer_lan_ipv4(addrs)
    )
    for ip in rows:
        if not is_loopback_ipv4(ip):
            return ip
    return rows[0] if rows else ""


def default_route_ipv4() -> str:
    """Source IPv4 the OS would use to reach the internet (the real LAN NIC).

    Empty when no route is available. This is the address a phone on the same
    LAN can actually reach — virtual NAT adapters never win here.
    """
    for dest in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(dest)
                ip = sock.getsockname()[0]
                if is_chosen_ipv4(str(ip)):
                    return str(ip)
        except OSError:
            continue
    return ""


def _is_virtual_nat_ipv4(host: str) -> bool:
    text = (host or "").strip()
    try:
        addr = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return any(addr in net for net in _VIRTUAL_NAT_NETS)


def reachable_lan_host(configured: str) -> str:
    """Best LAN host to advertise/bind for phone pairing.

    The default-route source is what a phone on the same LAN can reach. A
    configured host that is a virtual-NAT address (WSL/Docker/Hyper-V/CGNAT)
    is demoted in favor of the real route; an explicit non-virtual pick is
    kept. Loopback only when nothing else exists.
    """
    cfg = str(configured or "").strip()
    route = default_route_ipv4()
    if route and (not cfg or is_loopback_ipv4(cfg) or _is_virtual_nat_ipv4(cfg)):
        return route
    return cfg or pick_default_ipv4()


def list_candidate_ipv4() -> list[str]:
    """Host IPv4s excluding 0.0.0.0. Default-route source first; 127.0.0.1 last."""
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
    route = default_route_ipv4()
    if route:
        found.add(route)
    return prefer_lan_ipv4(found, preferred=(route,) if route else ())


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


_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def tailscale_ipv4() -> str:
    """IPv4 on the Tailscale tailnet (100.64.0.0/10), or ``""``.

    The tailnet address is reachable from a paired phone on Wi-Fi *and* mobile
    data (Tailscale DERP relays do the NAT traversal), so when Tailscale is up
    this becomes the primary QR candidate and a second Connect listener binds
    it. Empty when Tailscale is not installed/connected.
    """
    for ip in list_candidate_ipv4():
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if addr in _TAILSCALE_NET:
            return str(addr)
    return ""

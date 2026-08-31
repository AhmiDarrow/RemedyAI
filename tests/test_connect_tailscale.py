"""Tailscale transport: tailnet IP discovery + ``ts=`` QR line.

Tailscale gives RemedyConnect a universal path (Wi-Fi *and* mobile data via
DERP relays). These tests pin the two seams: discovering the 100.64.0.0/10
tailnet address, and advertising it in the pairing QR.
"""

from __future__ import annotations

from remedy.connect.bind import tailscale_ipv4
from remedy.connect.pair import start_pair


def _no_tailnet_candidates() -> list[str]:
    return ["192.168.0.46", "172.23.96.1", "127.0.0.1"]


def _with_tailnet_candidates() -> list[str]:
    return ["192.168.0.46", "100.101.102.103", "172.23.96.1", "127.0.0.1"]


def test_tailscale_ipv4_empty_when_no_tailnet(monkeypatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.list_candidate_ipv4", _no_tailnet_candidates)
    assert tailscale_ipv4() == ""


def test_tailscale_ipv4_finds_tailnet(monkeypatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.list_candidate_ipv4", _with_tailnet_candidates)
    assert tailscale_ipv4() == "100.101.102.103"


def test_start_pair_emits_ts_line(monkeypatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.list_candidate_ipv4", _with_tailnet_candidates)
    qr = start_pair(
        loopback=True,
        bind_host="192.168.0.46",
        bind_port=7401,
        tailscale="100.101.102.103",
    )
    assert "ts=100.101.102.103:7401" in qr
    assert "lan=192.168.0.46:7401" in qr
    # Belt: never leak local auth shapes into a pairing QR.
    assert "local_api_token" not in qr.lower()
    assert "bearer" not in qr.lower()


def test_start_pair_omits_ts_when_wildcard(monkeypatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.list_candidate_ipv4", _with_tailnet_candidates)
    qr = start_pair(loopback=True, bind_host="192.168.0.46", bind_port=7401, tailscale="0.0.0.0")
    assert "ts=" not in qr


def test_start_pair_omits_ts_when_not_an_ip(monkeypatch) -> None:
    monkeypatch.setattr("remedy.connect.bind.list_candidate_ipv4", _with_tailnet_candidates)
    qr = start_pair(loopback=True, bind_host="192.168.0.46", bind_port=7401, tailscale="not-an-ip")
    assert "ts=" not in qr


def test_server_tailscale_listener_is_extra(monkeypatch) -> None:
    """start_connect_server binds the tailnet address as a second listener."""
    import asyncio

    from remedy.connect import server as svc

    bound: list[str] = []
    real_start = asyncio.start_server

    async def fake_start(cb, host="", port=0, **kw):  # type: ignore[no-untyped-def]
        bound.append(str(host))
        return await real_start(cb, host=host, port=port, **kw)

    monkeypatch.setattr(asyncio, "start_server", fake_start)

    async def _run() -> None:
        srv = await svc.start_connect_server(
            "127.0.0.1",
            0,
            sidecar_port=7410,
            api_key="k",
            config={"connect_rdv_enabled": False},
            tailscale_host="100.101.102.103",
        )
        await svc.stop_connect_server()
        return srv

    asyncio.run(_run())
    assert "127.0.0.1" in bound
    assert "100.101.102.103" in bound

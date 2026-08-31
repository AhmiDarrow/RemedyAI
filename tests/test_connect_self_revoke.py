"""Phone self-revoke, revoke visibility, and provider-glance regressions.

Covers the Grove Connect fixes:
- a phone can revoke ITSELF through the pipe (not other devices),
- revoked devices disappear from the owner-visible device list,
- the read-only provider glance passes the pipe deny gate.
"""

from __future__ import annotations

import json

import pytest

from remedy.connect.pipe import HttpRequest, iter_request_http
from remedy.connect.store import (
    device_public_meta,
    get_device,
    revoke_device,
    save_device,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def _collect(resp: bytes) -> tuple[int, dict]:
    head, _, body = resp.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 1)[1].split(b" ", 1)[0])
    return status, json.loads(body.decode("utf-8", errors="replace"))


async def _call(req: HttpRequest, *, device: dict, config: dict | None = None) -> bytes:
    chunks = []
    async for piece in iter_request_http(
        req,
        device=device,
        sidecar_port=9,
        api_key="t",
        config=config,
    ):
        chunks.append(piece)
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_self_revoke_removes_record_and_drops_sessions(home, monkeypatch):
    did = "aa11bb22cc33dd44"
    save_device({"id": did, "name": "phone", "public_hex": "66" * 32}, home)
    dropped: list[str] = []

    from remedy.connect import lifecycle

    def fake_drop(device_id: str):
        dropped.append(device_id)

    monkeypatch.setattr(lifecycle, "drop_sessions_for_device", fake_drop)

    resp = await _call(
        HttpRequest(method="POST", path=f"/api/connect/devices/{did}/revoke", query=""),
        device={"id": did, "name": "phone"},
        config={"connect_panes": []},
    )
    status, body = _collect(resp)
    assert status == 200
    assert body.get("revoked") is True
    rec = get_device(did, home)
    assert rec is not None and rec.get("revoked") is True
    assert dropped == [did]


@pytest.mark.asyncio
async def test_self_revoke_refuses_other_devices(home):
    did = "aa11bb22cc33dd44"
    save_device({"id": did, "name": "phone", "public_hex": "66" * 32}, home)

    resp = await _call(
        HttpRequest(method="POST", path="/api/connect/devices/other99/revoke", query=""),
        device={"id": did, "name": "phone"},
        config={"connect_panes": []},
    )
    status, _body = _collect(resp)
    assert status == 403


def test_revoked_device_hidden_from_public_meta(home):
    did = "aa11bb22cc33dd44"
    save_device({"id": did, "name": "phone", "public_hex": "66" * 32}, home)
    assert [d["id"] for d in device_public_meta(home)] == [did]
    revoke_device(did, home)
    assert device_public_meta(home) == []


def test_provider_glance_allowed_through_deny(home):
    from remedy.connect.deny import connect_forbidden

    assert (
        connect_forbidden("GET", "/api/providers/connected", "", {"settings_write": False})
        is None
    )
    assert (
        connect_forbidden("POST", "/api/providers/connected", "", {"settings_write": False})
        is not None
    )

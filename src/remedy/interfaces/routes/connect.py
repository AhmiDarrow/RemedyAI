""":7400 management routes for Grove Connect (Bearer). Pair start is loopback-only."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from remedy.connect.panes import default_panes, normalize_panes
from remedy.connect.store import (
    device_public_meta,
    get_device,
    is_paused,
    revoke_device,
    set_paused,
)

logger = logging.getLogger(__name__)


class ConnectUpdate(BaseModel):
    enabled: bool | None = None
    bind_host: str | None = None
    bind_port: int | None = Field(default=None, ge=1, le=65535)
    paused: bool | None = None
    panes: dict[str, Any] | None = None
    relay_url: str | None = None


def _loopback_client(request: Request) -> bool:
    host = ""
    if request.client is not None:
        host = (request.client.host or "").strip().lower()
    if host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return True
    return host.startswith("127.") or host == "::ffff:127.0.0.1"


def _loopback_host_header(request: Request) -> bool:
    raw = (request.headers.get("host") or "").strip().lower()
    host_hdr = raw.split(":")[0].strip() if raw else ""
    if raw.startswith("["):
        # IPv6 literal [::1]:port
        end = raw.find("]")
        host_hdr = raw[1:end] if end > 0 else host_hdr
    if not host_hdr:
        return True
    return host_hdr in (
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
        "testserver",
        "[",
        "",
    )


def _require_loopback(request: Request) -> None:
    if not _loopback_client(request):
        raise HTTPException(status_code=403, detail="loopback only")
    if not _loopback_host_header(request):
        raise HTTPException(status_code=403, detail="host not loopback (rebinding blocked)")


def _refuse_connect_proxy(request: Request) -> None:
    """The Connect pipe originates on 127.0.0.1; hop header marks that path."""
    hop = (request.headers.get("x-remedy-connect-hop") or "").strip()
    if hop:
        raise HTTPException(status_code=403, detail="connect proxy cannot call connect management")


def _load_cfg() -> dict[str, Any]:
    from remedy.interfaces.api_support import load_config

    cfg = load_config()
    return cfg if isinstance(cfg, dict) else {}


def _validate_bind_for_enable(host: str) -> None:
    h = str(host or "").strip()
    if not h or h in ("0.0.0.0", "::", "[::]", "*"):
        raise HTTPException(status_code=400, detail="connect bind must be a chosen IPv4, not wildcard")
    try:
        from remedy.connect.bind import assert_chosen_bind, is_chosen_ipv4, is_wildcard_bind
    except ImportError:
        return
    try:
        if is_wildcard_bind(h) or not is_chosen_ipv4(h):
            raise HTTPException(
                status_code=400,
                detail="connect bind must be a chosen IPv4, not wildcard",
            )
        assert_chosen_bind(h)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "invalid connect bind") from exc


def _sidecar_port(app: FastAPI) -> int:
    try:
        p = int(getattr(app.state, "sidecar_port", 0) or 0)
    except (TypeError, ValueError):
        p = 0
    return p or 7400


def _api_key(app: FastAPI) -> str:
    return str(getattr(app.state, "api_key", "") or "")


def _snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        port = int(cfg.get("connect_bind_port") or 7401)
    except (TypeError, ValueError):
        port = 7401
    return {
        "enabled": bool(cfg.get("connect_enabled", False)),
        "bind_host": str(cfg.get("connect_bind_host") or "").strip(),
        "bind_port": port,
        "paused": bool(cfg.get("connect_paused", False)) or is_paused(),
        "panes": normalize_panes(cfg.get("connect_panes")),
        "relay_url": str(cfg.get("connect_relay_url") or "").strip(),
        "devices": device_public_meta(),
        "listening": None,
    }


def _connect_me_session_id() -> str | None:
    """Streaming turn first; focused desktop tab if nothing is on the wire."""
    active: list[str] = []
    with contextlib.suppress(Exception):
        from remedy.core.stream_lock import active_session_ids

        active = [str(s) for s in active_session_ids() if str(s).strip()]
    focused = ""
    with contextlib.suppress(Exception):
        from remedy.core.computer.host_bridge import get_host_bridge

        focused = str(get_host_bridge().focused_session_id() or "").strip()
    if focused and focused in active:
        return focused
    if active:
        return active[0]
    return focused or None


def _connect_me_payload() -> dict[str, Any]:
    cfg = _load_cfg()
    paused = bool(cfg.get("connect_paused", False)) or is_paused()
    turn_active = False
    with contextlib.suppress(Exception):
        from remedy.core.stream_lock import any_stream_active

        turn_active = bool(any_stream_active())
    return {
        "panes": normalize_panes(cfg.get("connect_panes")),
        "paused": paused,
        "reachable": "paused" if paused else "lan",
        "device_id": None,
        "session_id": _connect_me_session_id(),
        "turn_active": turn_active,
        "device": {"id": None, "name": None},
    }


def register_connect_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = runtime, gateway, memory

    @app.get("/connect/me")
    @app.get("/api/connect/me")
    async def connect_me():
        """Phone identity snapshot: focused/streaming chat id for native Stop."""
        return _connect_me_payload()

    @app.post("/api/stop")
    async def connect_stop():
        """Abort the /connect/me session; never the first GET /api/sessions row."""
        sid = _connect_me_session_id()
        if not sid:
            return {"status": "idle", "session_id": None, "notified": 0}
        from remedy.core.turn_context import abort_session as _abort_turn
        from remedy.core.turn_context import normalize_abort_reason

        reason_n = normalize_abort_reason("stop")
        n = _abort_turn(sid, reason=reason_n)
        return {
            "status": "aborted",
            "session_id": sid,
            "notified": n,
            "reason": reason_n,
        }

    @app.get("/api/connect")
    async def get_connect(request: Request):
        _refuse_connect_proxy(request)
        snap = _snapshot(_load_cfg())
        try:
            from remedy.connect.lifecycle import connect_listening_addr

            snap["listening"] = connect_listening_addr()
        except Exception:
            snap["listening"] = None
        return snap

    @app.put("/api/connect")
    async def put_connect(req: ConnectUpdate, request: Request):
        _refuse_connect_proxy(request)
        cfg = _load_cfg()
        enabled = bool(cfg.get("connect_enabled", False)) if req.enabled is None else bool(req.enabled)
        host = (
            str(cfg.get("connect_bind_host") or "").strip()
            if req.bind_host is None
            else str(req.bind_host).strip()
        )
        if enabled:
            _validate_bind_for_enable(host)
        patch: dict[str, Any] = {}
        if req.enabled is not None:
            patch["connect_enabled"] = bool(req.enabled)
        if req.bind_host is not None:
            patch["connect_bind_host"] = host
        if req.bind_port is not None:
            patch["connect_bind_port"] = int(req.bind_port)
        if req.paused is not None:
            patch["connect_paused"] = bool(req.paused)
        if req.panes is not None:
            patch["connect_panes"] = normalize_panes(req.panes)
        if req.relay_url is not None:
            patch["connect_relay_url"] = str(req.relay_url or "").strip()
        if not patch:
            return _snapshot(cfg)
        from remedy.interfaces.settings_apply import apply_settings_update

        try:
            await apply_settings_update(patch, runtime=runtime, gateway=gateway, memory=memory)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        fresh = _load_cfg()
        try:
            from remedy.connect.lifecycle import on_connect_settings_changed

            on_connect_settings_changed(fresh)
        except Exception:
            logger.debug("connect apply lifecycle", exc_info=True)
        if bool(fresh.get("connect_enabled")):
            try:
                from remedy.connect.lifecycle import maybe_start_connect

                maybe_start_connect(
                    request.app,
                    fresh,
                    api_key=_api_key(app),
                    sidecar_port=_sidecar_port(app),
                )
            except Exception:
                logger.warning("connect start after PUT failed", exc_info=True)
        return _snapshot(_load_cfg())

    @app.get("/api/connect/addresses")
    async def connect_addresses(request: Request):
        _refuse_connect_proxy(request)
        rows: list[str] = []
        try:
            from remedy.connect.bind import list_candidate_ipv4

            rows = [str(x) for x in (list_candidate_ipv4() or []) if str(x).strip()]
        except ImportError:
            rows = ["127.0.0.1"]
        except Exception:
            logger.debug("list_candidate_ipv4 failed", exc_info=True)
        return {"addresses": rows, "defaults": default_panes()}

    @app.post("/api/connect/pair/start")
    async def connect_pair_start(request: Request):
        _refuse_connect_proxy(request)
        _require_loopback(request)
        cfg = _load_cfg()
        host = str(cfg.get("connect_bind_host") or "").strip()
        try:
            port = int(cfg.get("connect_bind_port") or 7401)
        except (TypeError, ValueError):
            port = 7401
        if not host:
            try:
                from remedy.connect.bind import list_candidate_ipv4, prefer_lan_ipv4

                cands = prefer_lan_ipv4(list(list_candidate_ipv4() or []))
                host = str(cands[0]).strip() if cands else ""
            except Exception:
                host = ""
        if not host:
            raise HTTPException(
                status_code=400,
                detail="set connect_bind_host to a chosen IPv4 first",
            )
        from remedy.connect.pair import start_pair

        v6 = ""
        if bool(cfg.get("connect_allow_ipv6")):
            try:
                from remedy.connect.bind import list_candidate_ipv6

                g6 = list(list_candidate_ipv6() or [])
                if g6:
                    v6 = f"[{g6[0]}]:{port}"
            except Exception:
                v6 = ""
        relay = str(cfg.get("connect_relay_url") or "").strip()
        try:
            qr = start_pair(
                loopback=True,
                bind_host=host,
                bind_port=port,
                v6=v6,
                relay=relay,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="loopback only") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="connect keys unavailable") from exc
        exp = None
        for line in qr.splitlines():
            if line.startswith("exp="):
                try:
                    exp = int(line[4:].strip())
                except ValueError:
                    exp = None
        return {"qr": qr, "exp": exp}

    @app.post("/api/connect/devices/{device_id}/revoke")
    async def connect_revoke(device_id: str, request: Request):
        _refuse_connect_proxy(request)
        rec = get_device(device_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="device not found")
        revoke_device(device_id)
        try:
            from remedy.connect.lifecycle import drop_sessions_for_device

            drop_sessions_for_device(str(rec.get("id") or device_id))
        except Exception:
            try:
                from remedy.connect.lifecycle import drop_all_sessions

                drop_all_sessions()
            except Exception:
                pass
        return {"ok": True, "id": rec.get("id"), "revoked": True}

    @app.post("/api/connect/pause")
    async def connect_pause(request: Request):
        _refuse_connect_proxy(request)
        set_paused(True)
        cfg = _load_cfg()
        cfg["connect_paused"] = True
        from remedy.interfaces.settings_apply import apply_settings_update

        try:
            await apply_settings_update({"connect_paused": True}, runtime=runtime)
        except Exception:
            logger.debug("connect pause config", exc_info=True)
        try:
            from remedy.connect.lifecycle import drop_all_sessions

            drop_all_sessions()
        except Exception:
            pass
        return {"ok": True, "paused": True}

    @app.post("/api/connect/resume")
    async def connect_resume(request: Request):
        _refuse_connect_proxy(request)
        set_paused(False)
        try:
            from remedy.interfaces.settings_apply import apply_settings_update

            await apply_settings_update({"connect_paused": False}, runtime=runtime)
        except Exception:
            logger.debug("connect resume config", exc_info=True)
        return {"ok": True, "paused": False}

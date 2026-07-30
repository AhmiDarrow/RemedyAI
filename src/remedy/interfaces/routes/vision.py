"""REST API for the local visual decoder (llama.cpp + SmolVLM2 2.2B)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from remedy.interfaces.api_support import load_config

logger = logging.getLogger(__name__)


class VisionInstallRequest(BaseModel):
    model_id: str | None = None
    runtime_id: str | None = None
    prefer_cuda: bool = False


class VisionReinstallRuntimeRequest(BaseModel):
    prefer_cuda: bool = True


class VisionUninstallRequest(BaseModel):
    keep_models: bool = False


class VisionTestRequest(BaseModel):
    path: str | None = None
    question: str | None = None


class VisionSettingsPatch(BaseModel):
    enabled: bool | None = None
    model_id: str | None = None
    auto_start: bool | None = None
    n_gpu_layers: int | None = None


def register_vision_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register /api/vision/* routes."""

    @app.get("/api/vision/status")
    async def vision_status() -> dict[str, Any]:
        import asyncio

        from remedy.vision.service import get_status

        cfg = load_config()
        # Offload sync FS/port probes so concurrent /api/status stays responsive.
        return await asyncio.to_thread(get_status, cfg)

    @app.get("/api/vision/catalog")
    async def vision_catalog() -> dict[str, Any]:
        from remedy.vision.catalog import catalog_public

        return catalog_public()

    @app.post("/api/vision/activate")
    async def vision_activate() -> dict[str, Any]:
        """Activate prebundled/installed local VLM (SmolVLM2 — no download)."""
        from remedy.vision.catalog import DEFAULT_MODEL_ID
        from remedy.vision.service import activate_bundle

        cfg = load_config()
        try:
            from remedy.interfaces.api_support import _find_config_path, _write_config

            path = _find_config_path()
            if path is not None:
                vision = dict(cfg.get("vision") or {}) if isinstance(cfg.get("vision"), dict) else {}
                vision["enabled"] = True
                # Always pin product local model (never reintroduce retired ids).
                vision["model_id"] = DEFAULT_MODEL_ID
                cfg["vision"] = vision
                _write_config(path, cfg)
        except Exception:
            logger.exception("Failed to set vision.enabled in config")
        return activate_bundle(cfg=cfg, enabled=True)

    @app.post("/api/vision/install")
    async def vision_install(body: VisionInstallRequest | None = None) -> dict[str, Any]:
        """Activate bundle first; network download only as recovery for missing files."""
        from remedy.vision.service import start_install

        body = body or VisionInstallRequest()
        cfg = load_config()
        try:
            from remedy.interfaces.api_support import _find_config_path, _write_config

            path = _find_config_path()
            if path is not None:
                vision = dict(cfg.get("vision") or {}) if isinstance(cfg.get("vision"), dict) else {}
                vision["enabled"] = True
                # Always pin to single product model id (ignore alternate models)
                vision["model_id"] = "smolvlm2-2.2b"
                if body.runtime_id:
                    vision["runtime_id"] = body.runtime_id
                elif body.prefer_cuda:
                    vision["runtime_id"] = "win-cuda-12.4-x64"
                cfg["vision"] = vision
                _write_config(path, cfg)
        except Exception:
            logger.exception("Failed to set vision.enabled in config")
        return start_install(
            cfg=cfg,
            model_id="smolvlm2-2.2b",
            runtime_id=body.runtime_id,
            prefer_cuda=body.prefer_cuda,
        )

    @app.post("/api/vision/install/cancel")
    async def vision_install_cancel() -> dict[str, Any]:
        from remedy.vision.service import cancel_install

        return cancel_install()

    @app.post("/api/vision/reinstall-runtime")
    async def vision_reinstall_runtime(
        body: VisionReinstallRuntimeRequest | None = None,
    ) -> dict[str, Any]:
        """Reinstall llama-server only (CPU or CUDA); keep model weights when present."""
        from remedy.vision.service import reinstall_runtime

        body = body or VisionReinstallRuntimeRequest()
        cfg = load_config()
        return reinstall_runtime(cfg=cfg, prefer_cuda=bool(body.prefer_cuda))

    @app.post("/api/vision/uninstall")
    async def vision_uninstall(body: VisionUninstallRequest | None = None) -> dict[str, Any]:
        from remedy.vision.service import uninstall

        body = body or VisionUninstallRequest()
        cfg = load_config()
        result = uninstall(cfg=cfg, keep_models=body.keep_models)
        try:
            from remedy.interfaces.api_support import _find_config_path, _write_config

            path = _find_config_path()
            if path is not None:
                vision = dict(cfg.get("vision") or {}) if isinstance(cfg.get("vision"), dict) else {}
                vision["enabled"] = False
                cfg["vision"] = vision
                _write_config(path, cfg)
        except Exception:
            pass
        return result

    @app.post("/api/vision/start")
    async def vision_start() -> dict[str, Any]:
        from remedy.vision.service import ensure_server

        return ensure_server(load_config())

    @app.post("/api/vision/stop")
    async def vision_stop() -> dict[str, Any]:
        from remedy.vision.service import stop

        return stop(load_config())

    @app.post("/api/vision/test")
    async def vision_test(body: VisionTestRequest) -> dict[str, Any]:
        from pathlib import Path

        from remedy.vision.decoder import decode_image
        from remedy.vision.service import ensure_server, get_status

        cfg = load_config()
        status = get_status(cfg)
        if not status.get("installed"):
            return {"ok": False, "error": "Visual decoder not installed"}
        started = ensure_server(cfg)
        if not started.get("ok"):
            return started
        path = body.path
        p: Path | None = Path(path) if path else None
        if p is None or not p.is_file():
            # Default self-test image under ~/.remedy (create a tiny PNG if missing)
            try:
                from remedy.interfaces.config import load_config as _lc

                home = Path(
                    str((_lc() or {}).get("home_dir") or (Path.home() / ".remedy"))
                ).expanduser()
            except Exception:
                home = Path.home() / ".remedy"
            p = home / "tmp_e2e_vision.png"
            if not p.is_file():
                try:
                    import struct
                    import zlib

                    # Minimal 8x8 solid red PNG (no external deps)
                    def _png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
                        def chunk(tag: bytes, data: bytes) -> bytes:
                            return (
                                struct.pack(">I", len(data))
                                + tag
                                + data
                                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
                            )

                        raw = b"".join(
                            b"\x00" + bytes(rgb) * w for _ in range(h)
                        )
                        return (
                            b"\x89PNG\r\n\x1a\n"
                            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                            + chunk(b"IDAT", zlib.compress(raw, 9))
                            + chunk(b"IEND", b"")
                        )

                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(_png(8, 8, (220, 40, 40)))
                except Exception as exc:
                    return {
                        "ok": False,
                        "error": f"path required (and could not create test image: {exc})",
                    }
        if not p.is_file():
            return {"ok": False, "error": f"File not found: {path or p}"}
        base = status.get("base_url") or started.get("base_url")
        if not base:
            return {"ok": False, "error": "No base_url"}
        return decode_image(
            p,
            base_url=str(base),
            extra_question=body.question,
        )

"""Session API routes package."""
from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI, HTTPException

from remedy.interfaces.api_models import (
    SessionLlmRequest,
)
from remedy.interfaces.api_support import (
    load_config,
)

logger = logging.getLogger(__name__)



def register_llm_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register llm session routes."""
    _ = gateway  # may be unused in some modules

    @app.put("/api/sessions/{session_id}/llm")
    @app.post("/api/sessions/{session_id}/llm")
    async def set_session_llm(session_id: str, req: SessionLlmRequest):
        """Switch provider/model for this session (NanoToken remeasure on apply).

        Blocks while the runtime is streaming when detectable. Optionally
        persists as global default (make_default=true).
        """
        from remedy.interfaces.api_support import (
            _apply_llm_to_runtime,
            _default_config_path,
            _find_config_path,
            _write_config,
        )
        from remedy.interfaces.config import normalize_llm_settings

        # Only block if *this* session is streaming — other tabs may run freely.
        if runtime is not None:
            is_busy = False
            if hasattr(runtime, "is_session_streaming"):
                is_busy = bool(runtime.is_session_streaming(session_id))
            else:
                from remedy.core.turn_context import is_session_streaming

                is_busy = is_session_streaming(session_id)
            if is_busy:
                raise HTTPException(
                    409,
                    "Stop generation in this session before switching provider/model",
                )

        if memory is not None:
            row = await memory.get_chat_session(session_id)
            if row is None:
                raise HTTPException(404, "Session not found")

        cfg = load_config()
        raw_provider = (req.provider or cfg.get("llm_provider") or "openai")
        raw_model = (req.model or cfg.get("llm_model") or "").strip()
        # Fail closed on garbage ids (e.g. not-a-real-model-zzz) before toast/persist.
        try:
            from remedy.interfaces.config import validate_provider_model

            if raw_model:
                validate_provider_model(str(raw_provider), raw_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        provider, model, base_url = normalize_llm_settings(
            req.provider,
            req.model or cfg.get("llm_model"),
            cfg.get("llm_base_url") if str(req.provider).lower() == str(cfg.get("llm_provider") or "").lower() else None,
        )
        # Do not write live runtime._session_id / _last_send_messages — a
        # picker click on another tab must not poison the in-flight turn.

        # Resolve key for new provider
        api_key = None
        try:
            from remedy.interfaces.config import resolve_provider_api_key

            api_key = resolve_provider_api_key(cfg, provider)
        except Exception:
            api_key = None
        if not api_key and provider in ("ollama", "demo"):
            api_key = "local"

        # Session-only switch: persist the tab bind — do NOT thrash the shared
        # runtime or global config (other tabs may be streaming another provider).
        # make_default=true: also update global defaults + live runtime (Settings).
        if req.make_default:
            _apply_llm_to_runtime(
                runtime,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )

        # Always remember last model for this provider; global default only when asked
        config_path = _find_config_path() or _default_config_path()
        last_by = dict(cfg.get("last_model_by_provider") or {})
        last_by[provider] = model
        cfg["last_model_by_provider"] = last_by
        if req.make_default:
            cfg["llm_provider"] = provider
            cfg["llm_model"] = model
            cfg["llm_base_url"] = base_url
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            _write_config(config_path, cfg)
        except Exception as exc:
            logger.warning("session llm config write failed: %s", exc)

        # Persist per-session provider/model (tabs independent)
        if memory is not None:
            with contextlib.suppress(Exception):
                await memory.update_chat_session(
                    session_id, model=model, llm_provider=provider
                )

        # RMB is a single local host — status-bar model picks must reload GGUF
        # (not just rename the session's model string while 7B stays loaded).
        rmb_live = None
        if str(provider or "").lower() == "rmb" and model:
            try:
                import asyncio

                from remedy.core.turn_context import any_stream_claimed
                from remedy.runtime.rmb.service import apply_rmb_chat_model

                if any_stream_claimed():
                    raise HTTPException(
                        409,
                        "Cannot reload RMB while a session is streaming. "
                        "Stop the current turn first.",
                    )
                home = cfg.get("home_dir") if isinstance(cfg, dict) else None
                rmb_live = await asyncio.to_thread(
                    apply_rmb_chat_model,
                    str(model),
                    home_dir=home,
                    cfg=cfg if isinstance(cfg, dict) else None,
                    live=True,
                    wait_s=120.0,
                )
                live_path = (rmb_live or {}).get("model_path")
                if live_path:
                    from pathlib import Path as _P

                    stem = _P(str(live_path)).stem
                    if stem:
                        model = stem
                        if memory is not None:
                            with contextlib.suppress(Exception):
                                await memory.update_chat_session(
                                    session_id, model=model, llm_provider=provider
                                )
                        last_by = dict(cfg.get("last_model_by_provider") or {})
                        last_by["rmb"] = model
                        cfg["last_model_by_provider"] = last_by
                        if req.make_default:
                            cfg["llm_model"] = model
                        with contextlib.suppress(Exception):
                            _write_config(config_path, cfg)
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("session RMB model switch failed: %s", exc)
                rmb_live = {"ok": False, "error": str(exc)}

        remeasure = None
        with contextlib.suppress(Exception):
            from remedy.nanoswarm.token_nanobot import get_token_nanobot

            remeasure = get_token_nanobot().last_remeasure(session_id)

        window = None
        with contextlib.suppress(Exception):
            from remedy.nanoswarm.token_nanobot import resolve_context_window

            window = resolve_context_window(provider, model)

        toast = (
            f"Now using {provider} · {model}"
            + (f" · window {window}" if window else "")
            + (" · remeasured history" if remeasure else "")
        )
        if rmb_live is not None:
            live_err = (rmb_live.get("live_apply") or {}).get("live_error") or rmb_live.get(
                "error"
            )
            if live_err:
                toast = f"RMB model switch failed: {live_err}"
            elif rmb_live.get("live_note"):
                toast = f"{toast} · {rmb_live.get('live_note')}"
            elif (rmb_live.get("live_apply") or {}).get("restarted") or (
                rmb_live.get("live_apply") or {}
            ).get("started"):
                toast = f"{toast} · RMB host reloaded"

        return {
            "status": "ok",
            "session_id": session_id,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "make_default": req.make_default,
            "remeasure": remeasure,
            "context_window": window,
            "toast": toast,
            "rmb_live": (
                {
                    "model_id": rmb_live.get("model_id"),
                    "model_path": rmb_live.get("model_path"),
                    "live_error": (rmb_live.get("live_apply") or {}).get("live_error")
                    or rmb_live.get("error"),
                }
                if rmb_live is not None
                else None
            ),
        }

"""HTTP surface for the phone line — status, terms, and the owner's choice.

Phase 0 is a simulated line. These routes let Grove/Settings see what this
machine can actually do and record terms agreement. They do not place a
real PSTN call until a backend other than ``fake`` is ready.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from remedy.interfaces.api_support import load_config


class ChooseLineRequest(BaseModel):
    name: str


class TermsRequest(BaseModel):
    accept: bool = True


def _home(cfg: dict[str, Any] | None) -> str | None:
    if isinstance(cfg, dict) and cfg.get("home_dir"):
        return str(cfg["home_dir"])
    return None


def register_telephony_routes(
    app: FastAPI, *, runtime=None, gateway=None, memory=None
) -> None:
    _ = (runtime, gateway, memory)

    @app.get("/api/telephony/status")
    async def telephony_status() -> dict[str, Any]:
        from remedy.telephony import consent
        from remedy.telephony.options import chosen
        from remedy.telephony.registry import line_options, offer_lines

        cfg = load_config()
        home = _home(cfg)
        c = consent.read(home)
        opts = line_options(home)
        pick = chosen(home)
        return {
            "terms": {
                "agreed": c.current,
                "stale": c.stale,
                "version": c.version,
                "current_version": consent.TERMS_VERSION,
                "ask": consent.ask(home),
            },
            "chosen": pick,
            "offer": offer_lines(home),
            "lines": [
                {
                    "name": o.name,
                    "title": o.title,
                    "summary": o.summary,
                    "cost": o.cost,
                    "catch": o.catch,
                    "ready": o.ready,
                    "achievable": o.achievable,
                    "missing": list(o.missing),
                    "action": o.action,
                    "standalone": o.standalone,
                }
                for o in opts
            ],
            "real_line": False,
            "phase": 0,
            "loopback": True,
            "message": (
                "Calling a real number is not on this computer yet. "
                "The voice and turn-taking are ready; a loopback line can "
                "exercise them without dialling anyone. A SIP trunk is next."
            ),
        }

    @app.post("/api/telephony/terms")
    async def telephony_terms(req: TermsRequest) -> dict[str, Any]:
        from remedy.telephony import consent

        cfg = load_config()
        home = _home(cfg)
        if req.accept:
            c = consent.accept(home)
            return {"ok": True, "agreed": True, "version": c.version, "at": c.at}
        consent.withdraw(home)
        return {"ok": True, "agreed": False}

    @app.post("/api/telephony/choose")
    async def telephony_choose(req: ChooseLineRequest) -> dict[str, Any]:
        from remedy.telephony.options import choose
        from remedy.telephony.registry import line_options

        cfg = load_config()
        home = _home(cfg)
        wanted = (req.name or "").strip().lower()
        known = {o.name for o in line_options(home) if o.achievable}
        if wanted not in known:
            names = ", ".join(sorted(known)) or "none on this PC"
            return {
                "ok": False,
                "error": f"No line called {wanted!r} on this computer. Available: {names}.",
                "available": sorted(known),
            }
        name = choose(wanted, home=home)
        return {"ok": True, "chosen": name}

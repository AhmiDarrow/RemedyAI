"""TestClient-only: approvals, plans, partner status/metabolism.

Go ``remedy-runtime`` owns production ``:7400``; this registrar is for pytest.
Goals, life-tasks, checkpoints, memory/import, and identity pack routes are
Go-owned or unused by TestClient — not registered here.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from remedy.home import default_home

# Status-bar poll extras (swarm/health + approval config). Not session-scoped.
_SWARM_POLL_TTL = 12.0
_CFG_SYNC_TTL = 30.0
_poll_swarm: dict = {"ts": 0.0, "swarm": {}, "health": {}}
_cfg_sync_at = 0.0
_REMEDY_VERSION = ""


def _remedy_version() -> str:
    global _REMEDY_VERSION
    if _REMEDY_VERSION:
        return _REMEDY_VERSION
    ver = "0.24.0"
    with suppress(Exception):
        from importlib.metadata import version

        ver = version("remedy-ai")
    with suppress(Exception):
        import remedy

        ver = str(getattr(remedy, "__version__", ver) or ver)
    _REMEDY_VERSION = ver
    return ver


class ApprovalResolveRequest(BaseModel):
    approve: bool = True
    scope: str = Field(default="session", description="session | always")


class PlanCreateRequest(BaseModel):
    title: str
    goal: str = ""
    steps: list[str | dict] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    session_id: str | None = None
    status: str = "draft"


class PlanStatusRequest(BaseModel):
    status: str = Field(..., description="draft | approved | active | done | cancelled")


class PlanStepStatusRequest(BaseModel):
    status: str = Field(
        ..., description="pending | active | done | skipped"
    )
    step_id: str = Field(
        default="",
        description="Step id (s1), 1-based index, or title; optional if only plan_status",
    )
    plan_status: str = Field(
        default="",
        description="Optional plan-level status: draft|approved|active|done|cancelled",
    )


def register_partner_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = (gateway, memory)

    @app.get("/api/approvals")
    async def list_approvals(session_id: str | None = None):
        from remedy.core.approvals import APPROVALS

        items = APPROVALS.list_pending(session_id=session_id)
        return {"approvals": [APPROVALS.to_public(i) for i in items]}

    @app.post("/api/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, req: ApprovalResolveRequest):
        from remedy.core.approvals import APPROVALS

        item = APPROVALS.resolve(
            approval_id,
            approve=req.approve,
            scope=req.scope if req.scope in ("session", "always") else "session",
        )
        if item is None:
            raise HTTPException(404, "Approval not found")
        return {
            "status": item.status,
            "approval": APPROVALS.to_public(item),
            "hint": (
                "Approved — ask Remedy to retry the same command."
                if item.status == "approved"
                else "Denied — do not run the command."
            ),
        }

    def _life_home():
        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        return home

    def _plan_store():
        from remedy.core.plan_store import PlanStore

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        return PlanStore(home or default_home())

    @app.get("/api/plans")
    async def list_plans(session_id: str | None = None, limit: int = 30):
        store = _plan_store()
        plans = store.list_plans(session_id=session_id, limit=min(max(limit, 1), 100))
        return {"plans": [p.to_dict() for p in plans]}

    @app.get("/api/plans/latest")
    async def latest_plan(
        session_id: str | None = None,
        actionable: bool = False,
    ):
        """Latest plan for the session (or global latest when session_id omitted).

        When *session_id* is provided, never fall back to another session's plan —
        a fresh chat must show an empty Plan banner until plan_save in that session.

        *actionable*=true skips done/cancelled (Plan banner / Build kickoff).
        """
        store = _plan_store()
        plan = store.latest_for_session(
            session_id if session_id else None,
            actionable_only=bool(actionable),
        )
        if plan is None:
            return {"plan": None}
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.get("/api/plans/{plan_id}")
    async def get_plan(plan_id: str):
        store = _plan_store()
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, "Plan not found")
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.post("/api/plans")
    async def create_plan(req: PlanCreateRequest):
        store = _plan_store()
        plan = store.create(
            req.title.strip(),
            goal=req.goal or req.title,
            steps=list(req.steps or []),
            risks=list(req.risks or []),
            session_id=req.session_id,
            status=req.status or "draft",
        )
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.post("/api/plans/{plan_id}/status")
    async def set_plan_status(plan_id: str, req: PlanStatusRequest):
        store = _plan_store()
        plan = store.set_status(plan_id, req.status.strip().lower())
        if plan is None:
            raise HTTPException(404, "Plan not found or invalid status")
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.post("/api/plans/{plan_id}/steps/status")
    async def set_plan_step_status(plan_id: str, req: PlanStepStatusRequest):
        """Update one plan step (and optionally plan-level status)."""
        store = _plan_store()
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, "Plan not found")
        step_id = (req.step_id or "").strip()
        st = (req.status or "").strip().lower()
        if not step_id:
            raise HTTPException(400, "step_id is required")
        if st not in ("pending", "active", "done", "skipped"):
            raise HTTPException(
                400, "status must be pending | active | done | skipped"
            )
        updated = store.update_step_status(plan_id, step_id, st)
        if updated is None:
            raise HTTPException(404, "Step not found or invalid status")
        pst = (req.plan_status or "").strip().lower()
        if pst in ("draft", "approved", "active", "done", "cancelled"):
            bumped = store.set_status(updated.id, pst)
            if bumped is not None:
                updated = bumped
        return {"plan": updated.to_dict(), "markdown": updated.summary_markdown()}

    @app.get("/api/partner/status")
    async def partner_status(session_id: str | None = None):
        """Compact status for desktop status bar / harness chip.

        *session_id* scopes quality/metabolism to the focused chat tab (multi-tab
        desktop). When omitted, falls back to runtime's last session id.
        Metabolism is always lean (counters only) — full organs on
        ``GET /api/partner/metabolism``.
        """
        from remedy.core.approvals import APPROVALS

        # Config sync is not per-tab — do it a few times a minute, not every poll.
        global _cfg_sync_at
        now_poll = time.time()
        if now_poll - _cfg_sync_at >= _CFG_SYNC_TTL:
            try:
                from remedy.interfaces.api_support import load_config

                APPROVALS.sync_from_config(load_config() or {})
                _cfg_sync_at = now_poll
            except Exception:
                pass
        # Approvals stay global (missed approve on another tab still surfaces).
        # session_id only scopes quality/metabolism to the focused chat.
        sid_q = (session_id or "").strip() or None
        pending = APPROVALS.list_pending()
        goals_open = 0
        harness = "auto"
        scope: Any = "project"
        brief_intent = ""
        organism: dict = {}
        with suppress(Exception):
            from remedy.core.metabolism.organism import status_pack

            organism = await asyncio.to_thread(status_pack, _life_home(), runtime)
            goals_open = int(organism.get("open_count") or 0)
            if not goals_open and organism.get("life_title"):
                goals_open = 1
        if runtime is not None:
            if (
                goals_open == 0
                and not organism.get("alive")
                and hasattr(runtime, "list_tasks")
            ):
                from remedy.models import TaskStatus

                goals_open = len(
                    [
                        t
                        for t in runtime.list_tasks()
                        if t.status
                        not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED)
                    ]
                )
            scope_src = getattr(runtime, "_access_scope", None) or getattr(
                runtime, "access_scope", lambda: "project"
            )
            scope = scope_src() if callable(scope_src) else scope_src
            harness = getattr(runtime, "_harness_mode", "auto")
            brief = getattr(runtime, "_session_brief", None)
            if brief is not None:
                brief_intent = getattr(brief, "intent", "") or ""
        swarm: dict = {}
        health_pub: dict = {}
        cached_sw = _poll_swarm
        if now_poll - float(cached_sw.get("ts") or 0) < _SWARM_POLL_TTL and cached_sw.get("swarm"):
            swarm = dict(cached_sw.get("swarm") or {})
            health_pub = dict(cached_sw.get("health") or {})
        else:
            try:
                from remedy.nanoswarm import get_swarm

                st = get_swarm().status()
                swarm = {
                    "active": True,
                    "event_count": st.get("event_count"),
                    "local_model_id": st.get("local_model_id"),
                    "last_event": st.get("last_event"),
                    "fill_pct": (st.get("bots") or {}).get("memory", {}).get("last_fill_pct"),
                    "token_method": (st.get("bots") or {}).get("token", {}).get("last_method"),
                }
                prov = getattr(runtime, "_llm_provider", None) if runtime is not None else None
                mod = getattr(runtime, "_llm_model", None) if runtime is not None else None
                connected: list[str] = []
                try:
                    from remedy.interfaces.api_support import load_config
                    from remedy.interfaces.config import get_provider_keys
                    from remedy.interfaces.secret_store import public_secret_status

                    cfg = load_config()
                    keys = get_provider_keys(cfg)
                    connected = list(keys.keys())
                    pub = public_secret_status()
                    for k in (pub.get("provider_keys_set") or {}):
                        if k not in connected:
                            connected.append(k)
                    for extra in ("demo", "ollama"):
                        if extra not in connected:
                            connected.append(extra)
                except Exception:
                    connected = ["demo", "ollama"]
                health_pub = get_swarm().health.failover_suggestion(
                    provider=str(prov) if prov else None,
                    model=str(mod) if mod else None,
                    connected_providers=connected,
                )
                _poll_swarm["ts"] = now_poll
                _poll_swarm["swarm"] = dict(swarm)
                _poll_swarm["health"] = dict(health_pub)
            except Exception:
                swarm = {"active": False}
                health_pub = {}

        # Focused tab wins; else runtime last-touch session (gateway / single chat).
        sid_meta = sid_q
        if not sid_meta and runtime is not None:
            raw = getattr(runtime, "_session_id", None)
            sid_meta = str(raw).strip() if raw else None
        sid_key = sid_meta or ""

        quality: dict = {}
        try:
            from remedy.core.session_quality import get_session_quality

            quality = get_session_quality(str(sid_key)).snapshot()
        except Exception:
            quality = {}

        metabolism: dict = {}
        try:
            from remedy.core.metabolism.turn import metabolism_poll_snapshot

            # Poll: EU/DU only. Full organs stay on GET /api/partner/metabolism.
            metabolism = metabolism_poll_snapshot(str(sid_key))
            qmeta = (quality or {}).get("metabolism") if isinstance(quality, dict) else None
            if isinstance(qmeta, dict) and qmeta.get("last_tier") is not None:
                metabolism["tier"] = qmeta.get("last_tier")
                metabolism["tier_label"] = f"L{qmeta.get('last_tier')}"
        except Exception:
            metabolism = {}

        # Somatic signals — prefer the organism body (already on this poll).
        # Fall back to soma.json / refresh only when vitals have no mood yet.
        soma: dict = {}
        with suppress(Exception):
            from remedy.core.metabolism.organism import soma_from_vitals

            soma = soma_from_vitals(organism)
        if not soma.get("label"):
            try:
                import time as _time

                from remedy.core.muscle_profile import muscle_from_runtime
                from remedy.memory.soul.somatic import load_soma_file, refresh_soma

                home = None
                if runtime is not None:
                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                cached = load_soma_file(home)
                age = (
                    _time.time() - float(cached.get("ts") or 0)
                    if isinstance(cached, dict)
                    else 1e9
                )
                if isinstance(cached, dict) and age < 20.0 and cached.get("label"):
                    soma = cached
                else:
                    muscle = muscle_from_runtime(runtime)
                    soma = refresh_soma(
                        home,
                        muscle_label=muscle.label,
                        muscle_provider=muscle.provider,
                    )
            except Exception:
                soma = {}

        active_title = str(organism.get("life_title") or "")
        next_action = str(organism.get("next_action") or "")
        last_did = str(organism.get("last_did") or "")
        last_step = {"did": last_did} if last_did else None
        life_folder = str(organism.get("life_folder") or "")
        cas_n = int(organism.get("cas_count") or 0)
        cas_pub = {"count": cas_n} if cas_n else {}
        return {
            "version": _remedy_version(),
            "pending_approvals": len(pending),
            "approval_mode": APPROVALS.mode,
            "open_goals": goals_open,
            "active_goal": active_title or None,
            "next_action": next_action or None,
            "last_step": last_step,
            "life_folder": life_folder or None,
            "cas": cas_pub or None,
            "organism": organism or None,
            "access_scope": scope,
            "harness_mode": harness,
            "brief_intent": brief_intent[:200],
            "session_id": sid_key or None,
            "approvals": [APPROVALS.to_public(i) for i in pending[:5]],
            # Advanced: only meaningful when user opted into Full+ in the UI
            "nanoswarm": swarm,
            "session_quality": quality,
            "provider_health": health_pub,
            "metabolism": metabolism,
            "soma": soma,
        }

    @app.get("/api/partner/metabolism")
    async def partner_metabolism(session_id: str | None = None):
        """Advanced: silent metabolism snapshot (tier, EU/DU, governor, map).

        Top-level ``tier`` / ``evidence_units`` / ``decision_units`` mirror the
        session quality counters so Advanced UI and operators need not dig
        through nested ``session_quality.metabolism``.
        """
        from remedy.core.metabolism.turn import metabolism_public_snapshot
        from remedy.core.session_quality import get_session_quality

        sid = (session_id or "").strip() or None
        if not sid and runtime is not None:
            raw = getattr(runtime, "_session_id", None)
            sid = str(raw).strip() if raw else None
        key = sid or "_default"
        qsnap = get_session_quality(key).snapshot()
        meta = metabolism_public_snapshot(key)
        # Prefer live quality counters; fall back to organ snapshots.
        qmeta = qsnap.get("metabolism") if isinstance(qsnap, dict) else None
        if not isinstance(qmeta, dict):
            qmeta = {}
        evid = meta.get("evidence") if isinstance(meta, dict) else None
        dec = meta.get("decisions") if isinstance(meta, dict) else None
        eu = qmeta.get("evidence_units")
        if eu is None and isinstance(evid, dict):
            eu = evid.get("evidence_units") or evid.get("unit_count")
        du = qmeta.get("decision_units")
        if du is None and isinstance(dec, dict):
            du = dec.get("decision_units")
        tier = qmeta.get("last_tier")
        if tier is None and isinstance(dec, dict):
            # last_tier_label like "L2_agency" → 2 when possible
            lab = str(dec.get("last_tier_label") or "")
            if lab.startswith("L") and len(lab) >= 2 and lab[1].isdigit():
                tier = int(lab[1])
        return {
            "session_id": key,
            "tier": int(tier) if tier is not None else None,
            "evidence_units": int(eu) if eu is not None else 0,
            "decision_units": int(du) if du is not None else 0,
            "session_quality": qsnap,
            "metabolism": meta,
        }


"""Frozen TurnContext — authority snapshot for one turn (v0.32 M1.1).

``turn_context.py`` ContextVars remain the implementation. This object is the
source of truth callers should pass; it is built once at turn start.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from remedy.core.llm_binding import LlmBinding, get_llm_binding
from remedy.execution.budgets import ExecutionBudget
from remedy.policy.capabilities import Capability


@dataclass(frozen=True, slots=True)
class IdentityContext:
    name: str = "Remedy"
    gender: str = "female"


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    project_raw: str | None
    active_path: str
    access_scope: str = "project"


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    mode: str = "ask"
    skip_ask: bool = False


@dataclass(frozen=True, slots=True)
class CancellationToken:
    """Cooperative abort. ``event`` is the turn's asyncio.Event."""

    event: asyncio.Event | None = None

    def is_cancelled(self) -> bool:
        return bool(self.event is not None and self.event.is_set())


@dataclass(frozen=True, slots=True)
class TurnContext:
    session_id: str
    turn_id: str
    identity: IdentityContext
    workspace: WorkspaceContext
    capabilities: frozenset[Capability]
    approval: ApprovalContext
    budget: ExecutionBudget
    llm: LlmBinding
    plan_mode: bool
    cancellation: CancellationToken


class TurnFactory:
    """Build a frozen TurnContext from the current ContextVar turn."""

    @staticmethod
    def create(
        *,
        access_scope: str | None = None,
        identity: IdentityContext | None = None,
        budget: ExecutionBudget | None = None,
        runtime: Any = None,
    ) -> TurnContext:
        from remedy.core import turn_context as tc
        from remedy.core.agent_identity import DEFAULT_GENDER, DEFAULT_NAME
        from remedy.core.workspace import normalize_access_scope

        sid = str(tc.current_session_id() or "").strip() or "anonymous"
        tid = tc.current_turn_id() or str(uuid4())
        ws = tc.current_turn_workspace()
        flags = tc._turn_react_flags.get()
        mode = str(getattr(flags, "approval_mode", "") or "ask")
        skip = bool(getattr(flags, "skip_ask", False))
        scope = normalize_access_scope(access_scope or "project")
        ident = identity or IdentityContext(name=DEFAULT_NAME, gender=DEFAULT_GENDER)
        bind = get_llm_binding(runtime)
        return TurnContext(
            session_id=sid,
            turn_id=tid,
            identity=ident,
            workspace=WorkspaceContext(
                project_raw=None if ws is None else ws.project_raw,
                active_path="" if ws is None else ws.active_path,
                access_scope=scope,
            ),
            capabilities=frozenset(),
            approval=ApprovalContext(mode=mode or "ask", skip_ask=skip),
            budget=budget or ExecutionBudget(),
            llm=bind,
            plan_mode=bool(tc.current_plan_mode()),
            cancellation=CancellationToken(event=tc.current_abort_event()),
        )

"""Typed models for Partner State Machine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:10]}" if prefix else uuid4().hex[:12]


class Subgoal(BaseModel):
    """One unit of agency work — tools inside stay protected until closed."""

    id: str = Field(default_factory=lambda: _id("sg-"))
    title: str = ""
    status: Literal["open", "closed", "parked"] = "open"
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    notes: str = ""
    opened_at: datetime = Field(default_factory=_now)
    closed_at: datetime | None = None
    parent_id: str | None = None

    def touch_tool(self, tool_call_id: str = "", name: str = "", path: str = "") -> None:
        if tool_call_id and tool_call_id not in self.tool_call_ids:
            self.tool_call_ids.append(tool_call_id)
            if len(self.tool_call_ids) > 80:
                self.tool_call_ids = self.tool_call_ids[-80:]
        if name and name not in self.tool_names:
            self.tool_names.append(name)
            if len(self.tool_names) > 40:
                self.tool_names = self.tool_names[-40:]
        p = (path or "").strip()
        if p and p not in self.paths:
            self.paths.append(p)
            if len(self.paths) > 40:
                self.paths = self.paths[-40:]


class ToolTxn(BaseModel):
    """One tool execution as a transaction (effectful memory)."""

    id: str = Field(default_factory=lambda: _id("tx-"))
    tool_call_id: str = ""
    name: str = ""
    args_digest: str = ""
    result_digest: str = ""
    effect: Literal["read", "write", "side_effect", "unknown"] = "unknown"
    artifacts: list[str] = Field(default_factory=list)
    outcome: Literal["ok", "err", "partial"] = "ok"
    claim: str = ""
    result_preview: str = ""
    offload_path: str | None = None
    subgoal_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    chars: int = 0


class WriteEntry(BaseModel):
    """Unverified write in the current turn/session write-set."""

    path: str
    tool: str = ""
    txn_id: str = ""
    verified: bool = False
    verified_how: str = ""
    updated_at: datetime = Field(default_factory=_now)


class GraphNode(BaseModel):
    """Epistemic node — durable operational truth."""

    id: str = Field(default_factory=lambda: _id("n-"))
    kind: Literal[
        "fact",
        "decision",
        "artifact",
        "commitment",
        "hypothesis",
        "skill_pattern",
        "affordance",
    ] = "fact"
    text: str = ""
    why: str = ""
    rejected: str = ""
    path: str = ""
    confidence: float = 0.8
    source: str = "agent"  # agent | user | tool | local_core
    status: Literal["active", "superseded", "decayed", "open", "closed"] = "active"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_confirmed_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = _now()


class GraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: _id("e-"))
    src: str
    dst: str
    rel: Literal[
        "supports",
        "contradicts",
        "supersedes",
        "depends_on",
        "produced_by",
        "related",
    ] = "related"


class ProspectiveItem(BaseModel):
    """Future obligation — fire when trigger matches."""

    id: str = Field(default_factory=lambda: _id("pr-"))
    text: str = ""
    trigger: Literal[
        "session_start",
        "subgoal_close",
        "tool_success",
        "tool_name",
        "project_switch",
        "tests_pass",
        "epoch_roll",
        "manual",
    ] = "manual"
    tool_name: str = ""  # when trigger == tool_name
    project_path: str = ""
    armed: bool = True
    fired_count: int = 0
    max_fires: int = 3
    created_at: datetime = Field(default_factory=_now)
    last_fired_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

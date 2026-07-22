"""Shared Pydantic models for the Remedy framework.

These models form the data contract across all Remedy modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SkillKind(StrEnum):
    """Classification of a skill by its origin format."""

    NATIVE = "native"
    HERMES = "hermes"
    OPENCLAW = "openclaw"
    MCP = "mcp"


class SkillStatus(StrEnum):
    """Lifecycle status of a skill in the registry."""

    DISCOVERED = "discovered"
    VALIDATED = "validated"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class MemoryEntryType(StrEnum):
    """Type of memory entry for categorization and search."""

    NOTE = "note"
    HANDOFF = "handoff"
    SESSION = "session"
    USER_FACT = "user_fact"
    SKILL_LEARNED = "skill_learned"
    TASK_RESULT = "task_result"
    CONVERSATION = "conversation"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    """Lifecycle of a single task."""

    CREATED = "created"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    """Kinds of gateway events from external channels."""

    MESSAGE = "message"
    COMMAND = "command"
    CALLBACK = "callback"
    HEARTBEAT = "heartbeat"
    WEBHOOK = "webhook"
    TIMER = "timer"


class ChannelKind(StrEnum):
    """Supported communication channel types."""

    CLI = "cli"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    WEB = "web"
    API = "api"


class ToolSource(StrEnum):
    """Origin of a tool definition."""

    MCP = "mcp"
    SKILL = "skill"
    BUILTIN = "builtin"
    PLUGIN = "plugin"


class SkillManifest(BaseModel):
    """Metadata parsed from a SKILL.md frontmatter (agentskills.io format).

    See https://agentskills.io/spec for the canonical specification.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Unique skill identifier (kebab-case recommended)")
    description: str = Field(description="Short summary of what the skill does")
    version: str = Field(default="1.0.0", description="SemVer version")
    author: Optional[str] = Field(default=None, description="Skill author or source")
    license: Optional[str] = Field(default=None, description="SPDX license identifier")
    tags: list[str] = Field(default_factory=list, description="Discoverability tags")
    kind: SkillKind = Field(default=SkillKind.NATIVE)
    status: SkillStatus = Field(default=SkillStatus.DISCOVERED)
    homepage: Optional[str] = Field(default=None)
    repository: Optional[str] = Field(default=None)
    requires: list[str] = Field(default_factory=list, description="Dependencies (pip packages)")
    tools: list[str] = Field(default_factory=list, description="MCP tool names this skill uses")
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)

    # File-system metadata (populated at load time)
    path: Optional[str] = Field(default=None, description="Filesystem path to the skill directory")
    loaded_at: Optional[datetime] = Field(default=None)

    # Runtime metadata (populated by adapters, validators, etc.)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_native(self) -> bool:
        return self.kind == SkillKind.NATIVE


class Skill(BaseModel):
    """Runtime representation of a loaded skill.

    Includes manifest, instruction content, and references to bundled resources.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(default_factory=uuid4)
    manifest: SkillManifest
    instructions: str = Field(default="", description="Markdown body of SKILL.md")
    scripts: list[str] = Field(
        default_factory=list,
        description="Relative paths to bundled scripts (scripts/ directory)",
    )
    references: list[str] = Field(
        default_factory=list,
        description="Relative paths to reference documents (references/ directory)",
    )
    source_skill_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the original skill directory on disk",
    )


class MemoryEntry(BaseModel):
    """A single timestamped entry in the persistent memory store."""

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(default_factory=uuid4)
    entry_type: MemoryEntryType = Field(default=MemoryEntryType.NOTE)
    title: str = Field(default="")
    content: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: Optional[str] = Field(default=None)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class HandoffNote(BaseModel):
    """Structured handoff note passed between sessions or tasks.

    Designed for Remedy/Reme companion continuity across conversations.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(default_factory=uuid4)
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    from_session: Optional[str] = Field(default=None)
    to_session: Optional[str] = Field(default=None)
    context_summary: Optional[str] = Field(default=None)
    action_items: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = Field(default=False)


class SessionSummary(BaseModel):
    """Summary produced after a session ends."""

    session_id: str
    started_at: datetime
    ended_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tasks_completed: int = 0
    skills_created: int = 0
    skills_refined: int = 0
    key_decisions: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    summary: str = Field(default="")


class Task(BaseModel):
    """A single task tracked through the core runtime."""

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = Field(default="")
    status: TaskStatus = Field(default=TaskStatus.CREATED)
    parent_id: Optional[UUID] = Field(default=None)
    sub_tasks: list[UUID] = Field(default_factory=list)
    assigned_skill: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(default=None)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    result_summary: Optional[str] = Field(default=None)


class ToolDefinition(BaseModel):
    """Description of an available tool (MCP, skill, or builtin)."""

    name: str
    description: str
    source: ToolSource
    parameters: dict[str, Any] = Field(default_factory=dict)
    uri: Optional[str] = Field(default=None)


class ToolCall(BaseModel):
    """A request to invoke a tool."""

    id: UUID = Field(default_factory=uuid4)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[UUID] = Field(default=None)
    source: Optional[ToolSource] = Field(default=None)
    approved: bool = Field(default=False)


class ToolResult(BaseModel):
    """Result returned from a tool invocation."""

    call_id: UUID
    success: bool
    data: Any = Field(default=None)
    error: Optional[str] = Field(default=None)
    duration_ms: Optional[float] = Field(default=None)


class GatewayEvent(BaseModel):
    """An event received through the gateway from an external channel."""

    id: UUID = Field(default_factory=uuid4)
    kind: EventKind
    channel: ChannelKind
    source_id: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: Optional[str] = Field(default=None)
    raw: Optional[str] = Field(default=None)


class AgentConfig(BaseModel):
    """Configuration for the Remedy agent runtime."""

    name: str = Field(default="Remedy")
    persona: str = Field(default="default")
    home_dir: str = Field(default="~/.remedy")
    skills_dir: list[str] = Field(default_factory=list)
    memory_db_path: Optional[str] = Field(default=None)
    enabled_channels: list[ChannelKind] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    allow_skill_creation: bool = Field(default=True)
    auto_approve_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    log_level: str = Field(default="INFO")
    sarcasm_mode: bool = Field(default=False)

"""Core agent runtime -- the intelligent "brain" of Remedy.

Orchestrates planning, tool use, sub-agent delegation, and skill invocation.
Inspired by Hermes' ReAct loop with learning and self-improvement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from remedy.memory.handoff import AutoHandoffManager
from remedy.memory.profile import UserProfile
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    GatewayEvent,
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from remedy.skills.registry import SkillRegistry


class AgentRuntime(ABC):
    """Abstract base for the intelligent agent runtime.

    Concrete implementations plug in LLM backends, tool executors,
    and channel adapters. This interface defines the contract that
    the gateway, CLI, and other components use.
    """

    def __init__(self, config: AgentConfig, memory: MemoryStore | None = None) -> None:
        self.config = config
        self.memory = memory or MemoryStore(
            config.memory_db_path
            or f"{config.home_dir}/memory.db"
        )
        self.skills = SkillRegistry()
        self.handoff = AutoHandoffManager(self.memory)
        self._tasks: dict[UUID, Task] = {}
        self._session_id: str | None = None
        self._session_started_at: datetime | None = None
        self._user_profile: UserProfile | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Initialize subsystems and begin listening."""
        await self.memory.initialize()

    async def stop(self) -> None:
        """Gracefully shut down."""
        await self.memory.close()

    # -- task management -----------------------------------------------------

    def create_task(
        self,
        title: str,
        description: str = "",
        parent_id: UUID | None = None,
        tags: list[str] | None = None,
    ) -> Task:
        task = Task(
            id=uuid4(),
            title=title,
            description=description,
            parent_id=parent_id,
            tags=tags or [],
        )
        self._tasks[task.id] = task
        return task

    def get_task(self, task_id: UUID) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        if status is None:
            return list(self._tasks.values())
        return [t for t in self._tasks.values() if t.status == status]

    # -- event handling ------------------------------------------------------

    @abstractmethod
    async def handle_event(self, event: GatewayEvent) -> AsyncIterator[Any]:
        """Process an incoming gateway event and yield responses.

        This is the main entry point for channel events. Concrete runtimes
        implement routing, LLM calls, and tool use here.
        """
        ...
        yield  # pragma: no cover — makes this an async generator for type checkers

    # -- tool execution ------------------------------------------------------

    @abstractmethod
    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool and return the result."""
        ...

    # -- memory --------------------------------------------------------------

    async def remember(self, content: str, title: str = "", importance: float = 0.5) -> MemoryEntry:
        entry = MemoryEntry(
            title=title or f"Memory {datetime.now(UTC).isoformat()}",
            content=content,
            entry_type=MemoryEntryType.NOTE,
            importance=importance,
        )
        return await self.memory.upsert(entry)

    async def recall(self, query: str, limit: int = 10) -> list[MemoryEntry]:
        return await self.memory.search(query, limit=limit)

    # -- handoff -------------------------------------------------------------

    async def create_handoff(
        self,
        title: str,
        content: str,
        action_items: list[str] | None = None,
        decisions: list[str] | None = None,
        context_summary: str | None = None,
    ) -> HandoffNote:
        note = HandoffNote(
            title=title,
            content=content,
            action_items=action_items or [],
            decisions=decisions or [],
            context_summary=context_summary,
        )
        return await self.memory.create_handoff(note)

    async def get_relevant_handoffs(self, query: str, limit: int = 5) -> list[HandoffNote]:
        return await self.memory.get_relevant_handoffs(query, limit=limit)

    # -- sessions with auto-handoff -------------------------------------------

    async def start_session(self, session_id: str | None = None) -> str:
        """Begin a new session, loading pending handoffs and user profile."""
        self._session_id = session_id or str(uuid4())
        self._session_started_at = datetime.now(UTC)

        # Load user profile
        self._user_profile = await self.memory.get_or_create_profile()

        # Log session start
        await self.remember(
            content=f"Session {self._session_id} started.",
            title="Session started",
            importance=0.3,
        )

        # Load pending handoffs for continuity
        pending = await self.handoff.get_pending_handoffs()
        if pending:
            await self.remember(
                content=f"Loaded {len(pending)} pending handoff notes from previous sessions.",
                title="Pending handoffs loaded",
                importance=0.7,
            )

        return self._session_id

    async def end_session(self) -> HandoffNote | None:
        """End current session, auto-generating handoff and summary."""
        if self._session_id is None:
            return None

        completed = [
            t for t in self._tasks.values() if t.status == TaskStatus.COMPLETED
        ]
        open_tasks_list = [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS)
        ]

        # Update profile stats
        if self._session_started_at and self._user_profile:
            duration = (datetime.now(UTC) - self._session_started_at).total_seconds() / 60.0
            self._user_profile.record_session(duration)
            await self.memory.save_user_profile(self._user_profile)

        # Generate handoff
        handoff = await self.handoff.generate_handoff(
            session_id=self._session_id,
            tasks=completed,
            open_tasks=open_tasks_list,
        )

        # Generate session summary
        await self.handoff.generate_session_summary(
            session_id=self._session_id,
            started_at=self._session_started_at or datetime.now(UTC),
            tasks_completed=len(completed),
            key_decisions=[],
            open_items=[t.title for t in open_tasks_list],
        )

        # Clear session state
        self._session_id = None
        self._session_started_at = None
        self._tasks.clear()

        return handoff

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def user_profile(self) -> UserProfile | None:
        return self._user_profile

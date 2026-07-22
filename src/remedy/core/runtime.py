"""Core agent runtime -- the intelligent "brain" of Remedy.

Orchestrates planning, tool use, sub-agent delegation, and skill invocation.
Inspired by Hermes' ReAct loop with learning and self-improvement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from uuid import UUID, uuid4

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
from remedy.memory.store import MemoryStore
from remedy.skills.registry import SkillRegistry


class AgentRuntime(ABC):
    """Abstract base for the intelligent agent runtime.

    Concrete implementations plug in LLM backends, tool executors,
    and channel adapters. This interface defines the contract that
    the gateway, CLI, and other components use.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.memory = MemoryStore(
            config.memory_db_path
            or f"{config.home_dir}/memory.db"
        )
        self.skills = SkillRegistry()
        self._tasks: dict[UUID, Task] = {}

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
        parent_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
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

    def get_task(self, task_id: UUID) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[Task]:
        if status is None:
            return list(self._tasks.values())
        return [t for t in self._tasks.values() if t.status == status]

    # -- event handling ------------------------------------------------------

    async def handle_event(self, event: GatewayEvent) -> AsyncIterator[Any]:
        """Process an incoming gateway event and yield responses.

        This is the main entry point for channel events. The default
        implementation routes to the appropriate handler based on event kind.
        """
        yield f"[{self.config.name}] Received {event.kind.value} from {event.channel.value}"

    # -- tool execution ------------------------------------------------------

    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool and return the result."""
        raise NotImplementedError

    # -- memory --------------------------------------------------------------

    async def remember(self, content: str, title: str = "", importance: float = 0.5) -> MemoryEntry:
        entry = MemoryEntry(
            title=title or f"Memory {datetime.utcnow().isoformat()}",
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
        action_items: Optional[list[str]] = None,
        decisions: Optional[list[str]] = None,
        context_summary: Optional[str] = None,
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

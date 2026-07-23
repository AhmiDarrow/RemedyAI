"""Pydantic request/response models for the Remedy HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    session_id: str | None = Field(default=None)
    user_id: str | None = Field(default="default")
    channel: str | None = Field(default="api")


class ChatResponse(BaseModel):
    response: str
    request_id: str
    session_id: str | None = None
    processing_time_ms: float = 0.0


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class MemoryAddRequest(BaseModel):
    title: str = Field(..., description="Title for the memory entry")
    content: str = Field(..., description="Memory content")
    tags: list[str] = Field(default_factory=list, description="Optional tags")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class SkillInfo(BaseModel):
    name: str
    description: str
    version: str
    kind: str
    status: str
    tags: list[str] = []


class StatusResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime: str
    gateway: dict
    memory_entries: int = 0
    skills_count: int = 0
    sessions_count: int = 0
    chat_sessions_count: int = 0


class WebhookPayload(BaseModel):
    source: str
    event: str = "default"
    data: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Session")
    model: str | None = None
    agent: str | None = None
    project_path: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    agent: str | None = None
    project_path: str | None = None


class AttachmentRef(BaseModel):
    """Client-side reference to a previously uploaded session attachment."""

    path: str
    name: str | None = None
    mime: str | None = None
    size: int | None = None
    is_image: bool | None = None
    is_text: bool | None = None


class AttachmentUploadRequest(BaseModel):
    """JSON upload (preferred) — avoids python-multipart in frozen builds."""

    filename: str = Field(..., description="Original filename")
    content_type: str | None = Field(default=None, description="MIME type")
    data_base64: str = Field(..., description="Base64-encoded file bytes")


class SendMessageRequest(BaseModel):
    message: str = Field(default="", description="User message text")
    model: str | None = None
    agent: str | None = None
    attachments: list[AttachmentRef] | None = None


class CommandRequest(BaseModel):
    command: str = Field(..., description="Slash command to execute (e.g. /new)")


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    project_path: str | None = None
    name: str | None = None
    persona: str | None = None
    setup_completed: bool | None = None

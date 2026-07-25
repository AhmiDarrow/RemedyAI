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
    effort_weight: float = 0.0
    effort_band: str | None = None
    auto_generated: bool = False
    quarantine: bool = False
    success_rate: float | None = None
    path: str | None = None
    related: list[str] = []
    activations_session: int = 0
    lifecycle: str | None = None


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


class BulkSessionProjectRequest(BaseModel):
    """Move many sessions under one project (or clear with empty path)."""

    session_ids: list[str] = Field(default_factory=list, min_length=1)
    # Empty / null / "." → no project
    project_path: str | None = None


class SessionLlmRequest(BaseModel):
    """Mid-session provider/model switch (session override + hot runtime apply)."""

    provider: str = Field(..., min_length=1)
    model: str | None = None
    make_default: bool = False


class ImportSessionRequest(BaseModel):
    """Import a chat session from plain-text (or legacy markdown) export.

    Provide either ``text`` (preferred; client-read file) or ``path`` (server-side
    read under the configured access scope).
    """

    text: str | None = Field(default=None, description="Full .txt / .md session export body")
    path: str | None = Field(default=None, description="Local file path to a session export")
    title: str | None = Field(default=None, description="Override imported title")
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
    plan_mode: bool = Field(
        default=False,
        description="When true, only planning tools run (no shell/file mutation)",
    )


class CommandRequest(BaseModel):
    command: str = Field(..., description="Slash command to execute (e.g. /new)")


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    project_path: str | None = None
    name: str | None = None  # agent display name (what the AI is called)
    user_name: str | None = None  # human name (what Remedy calls the user)
    persona: str | None = None
    setup_completed: bool | None = None
    access_scope: str | None = None
    launch_at_login: bool | None = None
    start_in_tray: bool | None = None
    close_to_tray: bool | None = None
    harness_mode: str | None = None
    harness_min_context_pct: float | None = None
    harness_max_context_pct: float | None = None
    # Status-bar controls
    thinking_level: str | None = None  # off | low | medium | high
    approval_mode: str | None = None  # ask | auto (auto = full owner power / work-until-done)
    show_tool_calls: bool | None = None  # legacy → maps to tool_process
    # off = minimal progress only; medium = labels+status; full = near-raw process
    tool_process: str | None = None
    # Local visual decoder (llama.cpp + Qwen2.5-VL 3B)
    vision_enabled: bool | None = None
    vision_model_id: str | None = None
    # Prefer local image→text even when the chat model supports native vision
    # (saves provider image tokens / context when decoder is ready).
    vision_force_decode: bool | None = None
    # Opt-in web_fetch tool (public HTTP only; SSRF-guarded)
    web_tools_enabled: bool | None = None
    # Loopback HTTP may hand out the local API token (browser Web UI). Desktop
    # always prefers IPC. Default True; set False for IPC-only (safer).
    http_bootstrap: bool | None = None
    # Learning / advanced (safe defaults; never strips owner power)
    allow_skill_creation: bool | None = None
    auto_approve_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    log_level: str | None = None  # DEBUG | INFO | WARNING | ERROR
    sarcasm_mode: bool | None = None
    # Provider picker catalog
    enabled_providers: list[str] | None = None
    enabled_models: dict[str, list[str]] | None = None
    last_model_by_provider: dict[str, str] | None = None
    # Soft budget for active skills eligible for hot-catalog injection
    skills_active_budget: int | None = Field(default=None, ge=10, le=500)

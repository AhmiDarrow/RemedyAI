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


class PersonaWipeRequest(BaseModel):
    """Must send confirm='WIPE'. Does not delete chats, keys, or skills."""

    confirm: str = Field(..., description='Type WIPE to confirm')


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
    # Per-session LLM bind at create (multi-tab providers stay independent).
    llm_provider: str | None = None
    # Surface origin — "grove" marks the personal home chat so Studio's
    # raw-work session list can hide it.
    origin_channel: str | None = None


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
    """Preferred model id for this turn (paired with provider when set)."""
    provider: str | None = Field(
        default=None,
        description="Per-session LLM provider (xai, deepseek, …). Must pair with model.",
    )
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
    # User-set display name for the custom OpenAI-compatible endpoint (llama.cpp,
    # LM Studio, etc.). Shown in Settings and the status bar; empty → default label.
    custom_llm_name: str | None = None
    project_path: str | None = None
    name: str | None = None  # agent display name (what the AI is called)
    user_name: str | None = None  # human name (what Remedy calls the user)
    # female (default) | male | neutral — partner presentation / pronouns
    agent_gender: str | None = None
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
    approval_mode: str | None = None  # ask | auto (in-project) | full (warn)
    show_tool_calls: bool | None = None  # legacy → maps to tool_process
    # off = minimal progress only; medium = labels+status; full = near-raw process
    tool_process: str | None = None
    # Local visual decoder (llama.cpp + SmolVLM2 2.2B)
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
    # Opt-in: tighter tool caps + email/phone scrub before cloud LLM (default off = fast)
    privacy_mode: bool | None = None
    # Route cloud LLM traffic through local Sleev gateway (token compression).
    # Keys/models stay with the selected provider; only the request host changes.
    sleev_enabled: bool | None = None
    # Optional override (default: auto-discover Sleev install → 127.0.0.1:17321)
    sleev_gateway_url: str | None = None
    # Owner opt-in: allow non-loopback Sleev gateway (LAN/remote). Default off —
    # otherwise a non-local URL would receive provider API keys.
    sleev_allow_remote_gateway: bool | None = None
    # Maturity gates (experimental / advanced organs)
    soul_field_enabled: bool | None = None  # experimental personhood inject + tools
    build_os_advanced: bool | None = None  # Build OS frontiers A–H tools
    rmb_enabled: bool | None = None  # RMB local agent host allowed
    # Retention (days; 0 = never auto-purge that category). Nested or flat.
    retention_session_days: int | None = None
    retention_attachment_days: int | None = None
    retention_computer_shot_days: int | None = None
    retention_undo_days: int | None = None
    retention_log_days: int | None = None
    memory_encrypt: bool | None = None  # SQLCipher when available
    # Learning / advanced (safe defaults; never strips owner power)
    allow_skill_creation: bool | None = None
    # Bounds clamped in PUT handler (0..1) so partial/bulk saves never 422
    auto_approve_threshold: float | None = None
    log_level: str | None = None  # DEBUG | INFO | WARNING | ERROR
    sarcasm_mode: bool | None = None
    # Provider picker catalog
    enabled_providers: list[str] | None = None
    enabled_models: dict[str, list[str]] | None = None
    last_model_by_provider: dict[str, str] | None = None
    # Soft budget for active skills; clamped in PUT handler (10..500)
    skills_active_budget: int | None = None
    # In-app Browser slide homepage (http/https); empty → Remedy GitHub default
    browser_home_url: str | None = None
    # Messenger connectors (desktop Settings → Messengers)
    enabled_channels: list[str] | None = None
    # Per-messenger updates: { "telegram": { "bot_token": "…", "allow_chat_ids": "…", "clear_token": false } }
    messengers: dict[str, dict] | None = None
    # Personal assistant prefs (nested object; also stored under ~/.remedy/assistant.json)
    assistant: dict | None = None

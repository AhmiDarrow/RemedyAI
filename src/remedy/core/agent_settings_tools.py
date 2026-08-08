"""Agent tools so Remedy can configure itself when the user asks.

Examples the model should handle via these tools (not only UI instructions):
  - "enable web tools"
  - "set approval to auto"
  - "call me Ahmi"
  - "set up vision"
  - "switch to deepseek flash"
"""

from __future__ import annotations

import json
from typing import Any

from remedy.core.errors import format_tool_error


def register_settings_tools(runtime: Any) -> None:
    """Register get_settings + update_settings builtins."""

    def _memory():
        return getattr(runtime, "memory", None)

    def _gateway():
        return getattr(runtime, "gateway", None)

    async def get_settings() -> str:
        """Return current Remedy settings (no secrets)."""
        try:
            from remedy.interfaces.settings_apply import public_settings_snapshot

            snap = public_settings_snapshot()
            return json.dumps(snap, indent=2, default=str)
        except Exception as e:
            return format_tool_error(
                str(e),
                code="SETTINGS_READ_ERROR",
                tool_name="get_settings",
                suggestion="Retry; if config.toml is corrupt, open Settings and Save once.",
            )

    async def update_settings(
        settings: dict | str | None = None,
        setup: str = "",
        # Common flat fields (models prefer these over nested JSON)
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        name: str | None = None,
        user_name: str | None = None,
        agent_gender: str | None = None,
        persona: str | None = None,
        project_path: str | None = None,
        force_project_switch: bool | str = False,
        access_scope: str | None = None,
        thinking_level: str | None = None,
        approval_mode: str | None = None,
        tool_process: str | None = None,
        web_tools_enabled: bool | str | None = None,
        http_bootstrap: bool | str | None = None,
        vision_enabled: bool | str | None = None,
        vision_model_id: str | None = None,
        vision_force_decode: bool | str | None = None,
        harness_mode: str | None = None,
        browser_home_url: str | None = None,
        sarcasm_mode: bool | str | None = None,
        allow_skill_creation: bool | str | None = None,
        skills_active_budget: int | None = None,
        log_level: str | None = None,
        setup_completed: bool | str | None = None,
        launch_at_login: bool | str | None = None,
        start_in_tray: bool | str | None = None,
        close_to_tray: bool | str | None = None,
        auto_approve_threshold: float | None = None,
        enabled_channels: list | str | None = None,
        assistant: dict | None = None,
        messengers: dict | None = None,
        **extra: Any,
    ) -> str:
        """Apply Remedy settings for the user. Use when they ask to set/configure/enable anything.

        Prefer this over telling them to open Settings manually.
        Pass either:
          - setup=\"web tools\" / \"auto approval\" / \"vision\" (phrase shortcuts), and/or
          - flat fields (web_tools_enabled=true, approval_mode=auto, …), and/or
          - settings={...} partial object.
        """
        try:
            from remedy.interfaces.settings_apply import (
                apply_settings_update,
                resolve_setup_phrase,
            )
        except Exception as e:
            return format_tool_error(
                f"settings module unavailable: {e}",
                code="SETTINGS_IMPORT",
                tool_name="update_settings",
            )

        patch: dict[str, Any] = {}

        # Phrase shortcuts first
        if setup and str(setup).strip():
            aliased = resolve_setup_phrase(str(setup))
            if aliased:
                patch.update(aliased)
            else:
                # Allow free-form "enable X" without exact alias — still useful message
                low = str(setup).strip().lower()
                if "web" in low and ("tool" in low or "fetch" in low or "enable" in low):
                    patch["web_tools_enabled"] = "disable" not in low and "off" not in low
                elif "approval" in low or "auto" in low and "ask" not in low:
                    patch["approval_mode"] = "ask" if "ask" in low else "auto"
                elif "vision" in low or "smol" in low:
                    patch["vision_enabled"] = "disable" not in low and "off" not in low
                    if patch.get("vision_enabled"):
                        patch["vision_model_id"] = "smolvlm2-2.2b"
                else:
                    return format_tool_error(
                        f"Unknown setup phrase: {setup!r}. "
                        "Pass explicit fields (e.g. web_tools_enabled=true, "
                        "approval_mode=auto, user_name=…, llm_provider=…).",
                        code="UNKNOWN_SETUP",
                        tool_name="update_settings",
                        suggestion=(
                            "Call get_settings first, then update_settings with explicit keys."
                        ),
                    )

        # Nested / JSON settings object
        if settings is not None:
            if isinstance(settings, str) and settings.strip():
                try:
                    parsed = json.loads(settings)
                except json.JSONDecodeError as e:
                    return format_tool_error(
                        f"settings is not valid JSON: {e}",
                        code="BAD_JSON",
                        tool_name="update_settings",
                    )
                if isinstance(parsed, dict):
                    patch.update(parsed)
            elif isinstance(settings, dict):
                patch.update(settings)

        flat = {
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "llm_base_url": llm_base_url,
            "llm_api_key": llm_api_key,
            "name": name,
            "user_name": user_name,
            "agent_gender": agent_gender,
            "persona": persona,
            "project_path": project_path,
            "access_scope": access_scope,
            "thinking_level": thinking_level,
            "approval_mode": approval_mode,
            "tool_process": tool_process,
            "web_tools_enabled": web_tools_enabled,
            "http_bootstrap": http_bootstrap,
            "vision_enabled": vision_enabled,
            "vision_model_id": vision_model_id,
            "vision_force_decode": vision_force_decode,
            "harness_mode": harness_mode,
            "browser_home_url": browser_home_url,
            "sarcasm_mode": sarcasm_mode,
            "allow_skill_creation": allow_skill_creation,
            "skills_active_budget": skills_active_budget,
            "log_level": log_level,
            "setup_completed": setup_completed,
            "launch_at_login": launch_at_login,
            "start_in_tray": start_in_tray,
            "close_to_tray": close_to_tray,
            "auto_approve_threshold": auto_approve_threshold,
            "enabled_channels": enabled_channels,
            "assistant": assistant,
            "messengers": messengers,
        }
        for k, v in flat.items():
            if v is not None and v != "":
                patch[k] = v
        # Absorb any extra known keys models invent
        for k, v in extra.items():
            if v is not None and k not in ("kwargs",):
                patch[k] = v

        if not patch:
            return format_tool_error(
                "No settings provided. Example: update_settings(web_tools_enabled=true) "
                "or update_settings(setup=\"web tools\") or "
                "update_settings(user_name=\"Ahmi\", approval_mode=\"auto\").",
                code="EMPTY_PATCH",
                tool_name="update_settings",
            )

        # Project focus jail: refuse silently retargeting to a sibling tree
        # (SecretSticky session → update_settings(project_path=SecretFolder)).
        # force_project_switch alone is NOT enough — requires UI/user approval.
        force_switch = force_project_switch in (True, "true", "1", "yes", "on")
        if "force_project_switch" in patch:
            fv = patch.pop("force_project_switch", None)
            if fv in (True, "true", "1", "yes", "on"):
                force_switch = True
        if "project_path" in patch:
            from remedy.core.approvals import APPROVALS
            from remedy.core.turn_context import turn_session_id
            from remedy.core.workspace import (
                is_unset_project_path,
                resolve_project_path,
            )

            new_raw = patch.get("project_path")
            try:
                bound = not bool(runtime.project_path_is_unset())
            except Exception as exc:
                return format_tool_error(
                    f"cannot read current project binding: {exc}",
                    code="PROJECT_JAIL",
                    tool_name="update_settings",
                    suggestion="Retry get_settings; project switch refused fail-closed.",
                )
            if not is_unset_project_path(new_raw) and bound:
                try:
                    cur = runtime.effective_project_path().resolve()
                    nxt = resolve_project_path(str(new_raw)).resolve()
                except Exception as exc:
                    return format_tool_error(
                        f"invalid project_path (refused): {exc}",
                        code="PROJECT_JAIL",
                        tool_name="update_settings",
                        suggestion="Pass an existing folder path under the intended tree.",
                    )
                if cur != nxt:
                    if not force_switch:
                        return format_tool_error(
                            (
                                f"refusing to switch project focus from {cur} to {nxt}. "
                                "Session/project write jail stays on the current tree. "
                                "Do not retarget sibling projects mid-work."
                            ),
                            code="PROJECT_JAIL",
                            tool_name="update_settings",
                            suggestion=(
                                "Keep editing under the current focus folder. "
                                "If the user explicitly asked to switch projects, call "
                                "update_settings(project_path=…, force_project_switch=true) "
                                "and wait for user approval in the UI."
                            ),
                        )
                    # Force path still needs human approval (model cannot free-bypass).
                    sid = turn_session_id(runtime)
                    cmd = f"switch_project_path:{nxt}"
                    ask_reason = (
                        APPROVALS.needs_ask(cmd, tool_name="update_settings")
                        or "Switch project focus requires approval"
                    )
                    if not APPROVALS.is_approved(
                        "update_settings", cmd, session_id=sid
                    ):
                        item = APPROVALS.create(
                            tool_name="update_settings",
                            command=cmd,
                            reason=ask_reason,
                            session_id=sid,
                        )
                        return (
                            f"APPROVAL_REQUIRED id={item.id}\n"
                            f"reason={ask_reason}\n"
                            f"from={cur}\n"
                            f"to={nxt}\n"
                            "Do not invent success. Tell the user this needs approval "
                            f"in the UI (or /approve {item.id}), then retry with "
                            "force_project_switch=true after they approve."
                        )

        try:
            result = await apply_settings_update(
                patch,
                runtime=runtime,
                gateway=_gateway(),
                memory=_memory(),
            )
            return json.dumps(result, indent=2, default=str)
        except ValueError as e:
            return format_tool_error(
                str(e),
                code="INVALID_SETTINGS",
                tool_name="update_settings",
                suggestion="Call get_settings for current values and allowed keys.",
            )
        except OSError as e:
            return format_tool_error(
                f"failed to write config: {e}",
                code="WRITE_ERROR",
                tool_name="update_settings",
            )
        except Exception as e:
            return format_tool_error(
                str(e),
                code="SETTINGS_APPLY_ERROR",
                tool_name="update_settings",
            )

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "get_settings",
        "Read current Remedy settings (provider, model, web tools, approval mode, "
        "vision, persona, project path, etc.). No secrets. Use before/after configure.",
        get_settings,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "update_settings",
        "Configure Remedy for the user — persist settings immediately. "
        "USE THIS when the user asks to set up / enable / change / configure anything "
        "(web tools, approval mode, model, vision, name, project folder, messengers, …). "
        "Do not only tell them to open Settings. "
        "Examples: setup='web tools'; web_tools_enabled=true; approval_mode='auto'; "
        "user_name='Ahmi'; llm_provider='deepseek'; llm_model='deepseek-v4-flash'; "
        "vision_enabled=true; access_scope='full'. "
        "project_path changes while a focus folder is bound require "
        "force_project_switch=true AND user approval (no silent retarget).",
        update_settings,
        {
            "type": "object",
            "properties": {
                "setup": {
                    "type": "string",
                    "description": (
                        "Short phrase shortcut: 'web tools', 'auto approval', "
                        "'vision', 'thinking medium', 'full access', 'finish setup', …"
                    ),
                },
                "force_project_switch": {
                    "type": "boolean",
                    "description": (
                        "Required (with user approval) when changing project_path "
                        "while another focus is bound. Alone is not enough — the UI "
                        "must approve. Never invent approval."
                    ),
                },
                "settings": {
                    "type": "object",
                    "description": "Partial settings object (same keys as Settings Save).",
                    "additionalProperties": True,
                },
                "llm_provider": {"type": "string"},
                "llm_model": {"type": "string"},
                "llm_base_url": {"type": "string"},
                "llm_api_key": {
                    "type": "string",
                    "description": "Stored in secure key store, never in config.toml",
                },
                "name": {
                    "type": "string",
                    "description": "Partner display name (default Remedy)",
                },
                "agent_gender": {
                    "type": "string",
                    "enum": ["female", "male", "neutral"],
                    "description": "Partner gender presentation (default female)",
                },
                "user_name": {
                    "type": "string",
                    "description": "What Remedy calls the human",
                },
                "persona": {"type": "string"},
                "project_path": {"type": "string"},
                "access_scope": {
                    "type": "string",
                    "description": "project | home | full",
                },
                "thinking_level": {
                    "type": "string",
                    "description": "off | low | medium | high",
                },
                "approval_mode": {
                    "type": "string",
                    "description": "ask | auto (auto = full owner power)",
                },
                "tool_process": {
                    "type": "string",
                    "description": "off | medium | full",
                },
                "web_tools_enabled": {
                    "type": "boolean",
                    "description": "Enable public web_fetch tool",
                },
                "http_bootstrap": {"type": "boolean"},
                "vision_enabled": {"type": "boolean"},
                "vision_model_id": {"type": "string"},
                "vision_force_decode": {"type": "boolean"},
                "harness_mode": {"type": "string"},
                "browser_home_url": {"type": "string"},
                "sarcasm_mode": {"type": "boolean"},
                "allow_skill_creation": {"type": "boolean"},
                "skills_active_budget": {"type": "integer"},
                "log_level": {"type": "string"},
                "setup_completed": {"type": "boolean"},
                "launch_at_login": {"type": "boolean"},
                "start_in_tray": {"type": "boolean"},
                "close_to_tray": {"type": "boolean"},
                "auto_approve_threshold": {"type": "number"},
                "enabled_channels": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "assistant": {
                    "type": "object",
                    "description": "PA prefs (privacy consents, brief, timezone)",
                    "additionalProperties": True,
                },
                "messengers": {
                    "type": "object",
                    "description": "Messenger connector updates (telegram bot_token, …)",
                    "additionalProperties": True,
                },
            },
        },
    )

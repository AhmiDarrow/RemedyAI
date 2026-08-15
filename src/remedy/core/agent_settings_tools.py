"""Agent tools so Remedy can configure itself when the user asks.

Examples the model should handle via these tools (not only UI instructions):
  - "enable web tools"
  - "set approval to auto"
  - "call me Ahmi"
  - "set up vision"
  - "switch to deepseek flash"
  - "configure Sleev" / "enable Sleev" / "save tokens with Sleev"
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from remedy.core.errors import format_tool_error

_MESSENGER_ALLOWLIST_KEYS = ("allow_chat_ids", "allow_ids", "allow_from")


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "enable",
        "enabled",
    )


def _saved_settings_cfg() -> dict[str, Any]:
    try:
        from remedy.interfaces.api_support import load_config

        raw = load_config() or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _url_host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        return (parsed.hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _is_loopback_host_url(url: str) -> bool:
    from remedy.interfaces.config import is_loopback_hostname

    return is_loopback_hostname(_url_host(url))


def _hosts_equivalent(url_a: str, url_b: str) -> bool:
    try:
        pa = urlparse(url_a if "://" in url_a else f"https://{url_a}")
        pb = urlparse(url_b if "://" in url_b else f"https://{url_b}")
    except Exception:
        return False
    ha = (pa.hostname or "").lower().rstrip(".")
    hb = (pb.hostname or "").lower().rstrip(".")
    if not ha or not hb:
        return False
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def _approval_required(runtime: Any, cmd: str, reason: str) -> str:
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import turn_session_id

    sid = turn_session_id(runtime)
    ask_reason = APPROVALS.needs_ask(cmd, tool_name="update_settings") or reason
    if APPROVALS.is_approved("update_settings", cmd, session_id=sid):
        return ""
    item = APPROVALS.create(
        tool_name="update_settings",
        command=cmd,
        reason=ask_reason,
        session_id=sid,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={ask_reason}\n"
        "Do not invent success. Tell the user this needs approval "
        f"in the UI (or /approve {item.id}), then retry."
    )


def _llm_base_url_needs_approval(new_url: str, patch: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Foreign (non-catalog, non-loopback) base URL can steal the stored key."""
    url = (new_url or "").strip()
    if not url:
        return False
    current = str(cfg.get("llm_base_url") or "").strip()
    if current and url.rstrip("/") == current.rstrip("/"):
        return False
    if _is_loopback_host_url(url):
        return False
    from remedy.interfaces.config import PROVIDER_CATALOG, infer_provider_from_base_url

    owner = infer_provider_from_base_url(url)
    target = str(patch.get("llm_provider") or cfg.get("llm_provider") or "").strip().lower()
    if owner and (not target or owner == target):
        return False
    if target:
        cat_url = str((PROVIDER_CATALOG.get(target) or {}).get("base_url") or "")
        if cat_url and _hosts_equivalent(url, cat_url):
            return False
    return True


def _allowlist_empty(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, (list, tuple, set)):
        return not any(str(x).strip() for x in val)
    s = str(val).strip()
    return s in ("", "[]", "none", "null")


def _messenger_widen(patch: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(cmd, reason)`` for allow_all, emptied allowlists, or new channels."""
    current_enabled = {
        str(x).strip().lower()
        for x in (cfg.get("enabled_channels") or [])
        if str(x).strip()
    }
    raw_chs = patch.get("enabled_channels")
    if raw_chs is not None:
        if isinstance(raw_chs, str):
            new_chs = {x.strip().lower() for x in raw_chs.split(",") if x.strip()}
        elif isinstance(raw_chs, list):
            new_chs = {str(x).strip().lower() for x in raw_chs if str(x).strip()}
        else:
            new_chs = set()
        turning_on = new_chs - current_enabled - {"cli", "web", "api"}
        if turning_on:
            dest = ",".join(sorted(turning_on))
            return (
                f"widen_messengers:enable:{dest}",
                f"Enabling messenger channel(s) {dest} requires approval",
            )

    messengers = patch.get("messengers")
    if not isinstance(messengers, dict):
        return None
    for mid, body in messengers.items():
        mid_s = str(mid or "").strip().lower()
        if not mid_s or not isinstance(body, dict):
            continue
        if body.get("enabled") is True and mid_s not in current_enabled:
            return (
                f"widen_messengers:enable:{mid_s}",
                f"Enabling messenger {mid_s} requires approval",
            )
        raw_section = cfg.get(mid_s)
        section = raw_section if isinstance(raw_section, dict) else {}
        if "allow_all" in body and _truthy(body.get("allow_all")):
            if not _truthy(section.get("allow_all")):
                return (
                    f"widen_messengers:allow_all:{mid_s}",
                    f"Setting {mid_s} allow_all=true requires approval",
                )
        for key in _MESSENGER_ALLOWLIST_KEYS:
            if key not in body:
                continue
            if _allowlist_empty(body.get(key)) and not _allowlist_empty(section.get(key)):
                return (
                    f"widen_messengers:empty:{mid_s}:{key}",
                    f"Emptying {mid_s} {key} requires approval",
                )
    return None


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
        sleev_enabled: bool | str | None = None,
        sleev_gateway_url: str | None = None,
        sleev_allow_remote_gateway: bool | str | None = None,
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
                elif re.search(r"\bapproval\b", low) or re.search(
                    r"\b(?:auto|ask|full)\s+mode\b", low
                ):
                    if re.search(r"\bask\b", low):
                        patch["approval_mode"] = "ask"
                    elif re.search(r"\bfull\b", low):
                        patch["approval_mode"] = "full"
                    else:
                        patch["approval_mode"] = "auto"
                elif "vision" in low or "smol" in low:
                    patch["vision_enabled"] = "disable" not in low and "off" not in low
                    if patch.get("vision_enabled"):
                        patch["vision_model_id"] = "smolvlm2-2.2b"
                elif "sleev" in low or "sleeve" in low or (
                    "token" in low and "compress" in low
                ):
                    # "configure sleev", "turn off sleev", "save tokens"…
                    off = any(
                        w in low
                        for w in ("disable", "off", "stop", "without", "no sleev")
                    )
                    patch["sleev_enabled"] = not off
                else:
                    return format_tool_error(
                        f"Unknown setup phrase: {setup!r}. "
                        "Pass explicit fields (e.g. web_tools_enabled=true, "
                        "sleev_enabled=true, approval_mode=auto, user_name=…, "
                        "llm_provider=…).",
                        code="UNKNOWN_SETUP",
                        tool_name="update_settings",
                        suggestion=(
                            "Call get_settings first, then update_settings with explicit keys. "
                            "For Sleev: setup='configure sleev' or sleev_enabled=true."
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
            "sleev_enabled": sleev_enabled,
            "sleev_gateway_url": sleev_gateway_url,
            "sleev_allow_remote_gateway": sleev_allow_remote_gateway,
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
            if v is None:
                continue
            if v == "" and k != "project_path":
                continue
            patch[k] = v
        # Absorb any extra known keys models invent
        for k, v in extra.items():
            if v is not None and k not in ("kwargs",):
                patch[k] = v

        if not patch:
            return format_tool_error(
                "No settings provided. Example: update_settings(web_tools_enabled=true) "
                "or update_settings(setup=\"configure sleev\") or "
                "update_settings(sleev_enabled=true) or "
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
                is_forbidden_project_path,
                is_unset_project_path,
                resolve_project_path,
            )

            new_raw = patch.get("project_path")
            if not is_unset_project_path(new_raw) and (
                is_forbidden_project_path(new_raw)
                or is_forbidden_project_path(resolve_project_path(str(new_raw)))
            ):
                return format_tool_error(
                    (
                        f"Project path is not allowed: {new_raw}. "
                        "Pick a user folder, not an OS or program directory."
                    ),
                    code="PROJECT_FORBIDDEN",
                    tool_name="update_settings",
                    suggestion="Choose a user project folder.",
                )
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
            elif bound and is_unset_project_path(new_raw):
                # Unset / "." drops the write jail to full profile — same as a switch.
                from remedy.core.approvals import APPROVALS
                from remedy.core.turn_context import turn_session_id

                sid = turn_session_id(runtime)
                cmd = "unset_project_path"
                ask_reason = (
                    APPROVALS.needs_ask(cmd, tool_name="update_settings")
                    or "Clearing the project focus (write jail) requires approval"
                )
                if not force_switch or not APPROVALS.is_approved(
                    "update_settings", cmd, session_id=sid
                ):
                    if not force_switch:
                        return format_tool_error(
                            (
                                "refusing to clear project_path while a focus folder "
                                "is bound (that would lift the write jail). "
                                "Keep working under the current tree."
                            ),
                            code="PROJECT_JAIL",
                            tool_name="update_settings",
                            suggestion=(
                                "If the user explicitly asked to detach the project, "
                                "call update_settings(project_path='', "
                                "force_project_switch=true) and wait for UI approval."
                            ),
                        )
                    item = APPROVALS.create(
                        tool_name="update_settings",
                        command=cmd,
                        reason=ask_reason,
                        session_id=sid,
                    )
                    return (
                        f"APPROVAL_REQUIRED id={item.id}\n"
                        f"reason={ask_reason}\n"
                        "Do not invent success. Tell the user this needs approval "
                        f"in the UI (or /approve {item.id})."
                    )

        # Widening scope / auto-approval is owner power — model cannot self-grant.
        widen_scope = str(patch.get("access_scope") or "").strip().lower()
        widen_approval = str(patch.get("approval_mode") or "").strip().lower()
        if widen_scope in ("home", "full") or widen_approval in ("auto", "full"):
            if widen_scope in ("home", "full"):
                cmd = f"widen_access_scope:{widen_scope}"
                reason = f"Raising access_scope to {widen_scope} requires approval"
            else:
                cmd = f"widen_approval_mode:{widen_approval}"
                reason = f"Switching approval_mode to {widen_approval} requires approval"
            locked = _approval_required(runtime, cmd, reason)
            if locked:
                return locked

        saved_cfg = _saved_settings_cfg()

        # Foreign llm_base_url would hot-reload the stored provider key to that host.
        if "llm_base_url" in patch and _llm_base_url_needs_approval(
            str(patch.get("llm_base_url") or ""), patch, saved_cfg
        ):
            dest = _url_host(str(patch.get("llm_base_url") or "")) or "unknown"
            locked = _approval_required(
                runtime,
                f"widen_llm_base_url:{dest}",
                "Changing llm_base_url to a non-catalog host requires approval",
            )
            if locked:
                return locked

        if "sleev_allow_remote_gateway" in patch and _truthy(
            patch.get("sleev_allow_remote_gateway")
        ):
            if not _truthy(saved_cfg.get("sleev_allow_remote_gateway")):
                locked = _approval_required(
                    runtime,
                    "widen_sleev_allow_remote",
                    "Enabling sleev_allow_remote_gateway requires approval",
                )
                if locked:
                    return locked

        if "sleev_gateway_url" in patch:
            sleev_url = str(patch.get("sleev_gateway_url") or "").strip()
            prev_sleev = str(saved_cfg.get("sleev_gateway_url") or "").strip()
            if (
                sleev_url
                and sleev_url.rstrip("/") != prev_sleev.rstrip("/")
                and not _is_loopback_host_url(sleev_url)
            ):
                dest = _url_host(sleev_url) or "unknown"
                locked = _approval_required(
                    runtime,
                    f"widen_sleev_gateway_url:{dest}",
                    "Setting a non-loopback sleev_gateway_url requires approval",
                )
                if locked:
                    return locked

        messenger_widen = _messenger_widen(patch, saved_cfg)
        if messenger_widen:
            cmd, messenger_reason = messenger_widen
            locked = _approval_required(runtime, cmd, messenger_reason)
            if locked:
                return locked

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
        "Read current Remedy settings (provider, model, Sleev routing, web tools, "
        "approval mode, vision, persona, project path, etc.). Includes sleev status "
        "(installed / gateway). No secrets. Use before/after configure.",
        get_settings,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "update_settings",
        "Configure Remedy for the user — persist settings immediately. "
        "USE THIS when the user asks to set up / enable / change / configure anything "
        "(Sleev token savings, web tools, approval mode, model, vision, name, "
        "project folder, messengers, …). "
        "Do not only tell them to open Settings. "
        "Examples: setup='configure sleev'; sleev_enabled=true; setup='web tools'; "
        "approval_mode='auto'; user_name='Ahmi'; llm_provider='deepseek'; "
        "llm_model='deepseek-v4-flash'; vision_enabled=true; access_scope='full'. "
        "After enabling Sleev, note it only routes cloud providers (xAI/DeepSeek/…); "
        "Ollama/RMB stay direct. User needs `sleev` CLI installed + gateway running. "
        "project_path changes or clearing the focus folder require "
        "force_project_switch=true AND user approval. Raising access_scope "
        "to home/full, approval_mode to auto, a foreign llm_base_url, "
        "remote Sleev, messengers allow_all / emptied allowlists, or "
        "enabling a channel also needs UI approval.",
        update_settings,
        {
            "type": "object",
            "properties": {
                "setup": {
                    "type": "string",
                    "description": (
                        "Short phrase shortcut: 'configure sleev', 'sleev off', "
                        "'web tools', 'auto approval', 'vision', 'thinking medium', "
                        "'full access', 'finish setup', …"
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
                    "description": "ask | auto (in-project) | full (warn)",
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
                "sleev_enabled": {
                    "type": "boolean",
                    "description": (
                        "Route cloud LLM chat through local Sleev gateway "
                        "(token compression). Requires sleev CLI + gateway."
                    ),
                },
                "sleev_gateway_url": {
                    "type": "string",
                    "description": (
                        "Optional Sleev gateway base (empty = auto-discover "
                        "127.0.0.1:17321). Non-loopback requires "
                        "sleev_allow_remote_gateway=true."
                    ),
                },
                "sleev_allow_remote_gateway": {
                    "type": "boolean",
                    "description": (
                        "Owner opt-in: allow LAN/remote Sleev gateway. Default false "
                        "so provider API keys stay on loopback."
                    ),
                },
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

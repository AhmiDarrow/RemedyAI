"""Sanitize chat payloads before they leave the machine to an LLM provider.

Trust boundary: tool results may contain mail snippets, page text, or paths.
Secrets (tokens, keys) must never be forwarded. Bodies are size-capped.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

# Max characters for a single tool/function message content when sending upstream.
TOOL_CONTENT_MAX = 6_000
# Local/RMB coding: enough for a file read/edit turn, not a full 32k dump.
# Endless context + harness further offload; this is the hard ceiling per message.
TOOL_CONTENT_MAX_LOCAL = 12_000
# Privacy mode: tighter tool caps (opt-in; default path unchanged for speed).
TOOL_CONTENT_MAX_PRIVACY = 2_500
# Tool *call* arguments must stay valid JSON — never mid-string clip (providers
# return HTTP 400 "EOF while parsing a string at column ~6000" if we truncate).
TOOL_ARGS_MAX = 48_000
# Nested string values inside tool-call JSON (after parse).
TOOL_ARGS_VALUE_MAX = 8_000
TOOL_ARGS_VALUE_MAX_PRIVACY = 2_000
# file_write content may be large; keep more for *in-turn* fidelity when small,
# but summarize past full-body dumps so history does not look half-written.
FILE_WRITE_CONTENT_HISTORY_MAX = 1_200
# Max for normal user/assistant text (already bounded in practice).
TEXT_CONTENT_MAX = 100_000
TEXT_CONTENT_MAX_PRIVACY = 40_000

# PII-ish patterns used only when privacy_mode is on (avoid cost on default path).
_EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-.]?\d{4}(?!\d)"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Tools whose results often carry personal content when privacy mode is on.
_PRIVACY_HEAVY_TOOLS = frozenset(
    {
        "gmail_search",
        "gmail_read",
        "gmail_get",
        "calendar_list",
        "calendar_get",
        "web_fetch",
        "computer_snapshot",
        "computer_page_text",
        "computer_a11y",
        "page_text",
        "snapshot",
        "browser_snapshot",
        "memory_search",
        "memory_get",
    }
)


# Tiny TTL cache so ReAct multi-round calls do not re-read config every POST.
_PRIVACY_CACHE: tuple[float, bool] | None = None
_PRIVACY_CACHE_TTL = 5.0  # seconds — settings changes apply quickly, still cheap


def privacy_mode_enabled() -> bool:
    """Whether outbound provider scrubbing should use privacy-mode rules.

    Priority: ``REMEDY_PRIVACY_MODE`` env → config.toml ``privacy_mode`` → False.
    Default **off** keeps the lightning path (secret scrub only, larger tool caps).
    Result is cached ~5s so multi-tool turns stay fast.
    """
    import os
    import time

    global _PRIVACY_CACHE
    now = time.monotonic()
    if _PRIVACY_CACHE is not None:
        ts, val = _PRIVACY_CACHE
        if now - ts < _PRIVACY_CACHE_TTL:
            return val

    flag = str(os.environ.get("REMEDY_PRIVACY_MODE", "")).strip().lower()
    if flag in ("1", "true", "yes", "on", "enable", "enabled"):
        val = True
    elif flag in ("0", "false", "no", "off", "disable", "disabled"):
        val = False
    else:
        val = False
        try:
            from remedy.interfaces.config import load_config

            cfg = load_config() or {}
            if "privacy_mode" in cfg:
                val = bool(cfg.get("privacy_mode"))
        except Exception:
            val = False
    _PRIVACY_CACHE = (now, val)
    return val


def clear_privacy_mode_cache() -> None:
    """Test helper: drop privacy-mode TTL cache."""
    global _PRIVACY_CACHE
    _PRIVACY_CACHE = None

# Tools whose large string args should be summarized in provider history
# (full bodies mislead the model after 8k value caps).
_WRITE_BODY_TOOLS = frozenset({"file_write", "write"})
_EDIT_BODY_TOOLS = frozenset({"file_edit", "file_edit_batch", "apply_patch"})
# Computer type/act often carry passwords or tokens — redact typed payloads in history.
_COMPUTER_TYPE_TOOLS = frozenset({"computer_type", "computer_act"})

_SECRET_KEY_RE = re.compile(
    r"(?i)(access_token|refresh_token|id_token|client_secret|authorization|"
    r"api_key|apikey|password|passwd|bearer|private_key|bot_token|app_secret|"
    r"signing_secret|session_token|x-api-key|auth_token|app_password|"
    r"cookie|set-cookie)"
)

# Fallback patterns when metabolism.redact is unavailable; keep in sync with
# remedy.core.metabolism.redact (Anthropic/OpenRouter/HF/npm/Stripe/Google/…).
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-or-[A-Za-z0-9_\-]{16,}"
    r"|sk-proj-[A-Za-z0-9_\-]{16,}"
    r"|sk-[a-zA-Z0-9]{20,}"
    r"|xai-[a-zA-Z0-9]{20,}"
    r"|ya29\.[a-zA-Z0-9._-]+"
    r"|1//[a-zA-Z0-9_-]+"
    r"|ghp_[a-zA-Z0-9]{20,}"
    r"|gho_[a-zA-Z0-9]{20,}"
    r"|xox[baprs]-[a-zA-Z0-9-]{10,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|npm_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z\-_]{20,}"
    r"|(?:sk_live|sk_test|rk_live|rk_test)_[A-Za-z0-9]{16,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r")\b"
)


def _clip(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)] + "…"


def _scrub_text(
    text: str,
    *,
    max_len: int,
    privacy: bool | None = None,
) -> str:
    """Redact secret-shaped substrings then size-cap for provider HTTP.

    Prefer shared metabolism redaction (broader patterns: PEM, DB URLs,
    Discord webhooks, …) so outbound chat matches ledger/IR fail-closed policy.

    When *privacy* is True (or privacy_mode is on), also strip email/phone/SSN
    shapes. Default path skips those regexes for speed.
    """
    raw = text or ""
    try:
        from remedy.core.metabolism.redact import redact_text

        t = redact_text(raw)
    except Exception:
        t = _SECRET_VALUE_RE.sub("[redacted]", raw)
    if privacy is None:
        privacy = privacy_mode_enabled()
    if privacy:
        t = _EMAIL_RE.sub("[email]", t)
        t = _PHONE_RE.sub("[phone]", t)
        t = _SSN_RE.sub("[ssn]", t)
    return _clip(t, max_len)


def _scrub_obj(obj: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[truncated]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                out[ks] = "[redacted]"
            else:
                out[ks] = _scrub_obj(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_scrub_obj(x, depth=depth + 1) for x in obj[:200]]
    if isinstance(obj, str):
        return _scrub_text(obj, max_len=TOOL_CONTENT_MAX)
    return obj


def _summarize_large_string(value: str, *, kind: str = "body") -> str:
    """Replace a huge string with a short, non-misleading history stub.

    Wording is intentionally *not* valid source code so the model cannot
    echo this stub back into ``file_write`` (that corrupted App.tsx once).
    """
    t = _SECRET_VALUE_RE.sub("[redacted]", value or "")
    n = len(t)
    lines = t.count("\n") + (1 if t else 0)
    first = ""
    for line in t.splitlines():
        if line.strip():
            first = line.strip()[:80]
            break
    return (
        f"<<NOT_SOURCE_CODE history_stub kind={kind} chars={n} lines~{lines} "
        f"first_line={first!r} "
        f"DO_NOT_file_write_this_string history_stub_only>>"
    )


def _rewrite_write_tool_args(parsed: Any, tool_name: str) -> Any:
    """For file_write/file_edit history: never ship truncated half-files upstream.

    Mid-value clipping made the model re-emit whole files / chunk scripts.
    Summarize large bodies instead; keep path and small metadata intact.
    """
    if not isinstance(parsed, dict):
        return parsed
    name = (tool_name or "").strip().lower()
    out = dict(parsed)

    if name in _WRITE_BODY_TOOLS:
        content = out.get("content")
        if isinstance(content, str) and len(content) > FILE_WRITE_CONTENT_HISTORY_MAX:
            # Partner rule: NEVER put a copy-pasteable stub in ``content``.
            # Models re-emitted <<NOT_SOURCE_CODE history_stub…>> as file_write
            # bodies (HISTORY_STUB ×N fail loop). Omit body; keep path + size only.
            n = len(content)
            out.pop("content", None)
            out["content"] = ""  # empty, not a stub string
            out["_content_chars"] = n
            out["_history_summarized"] = True
            out["_body_omitted"] = True
            out["history_note"] = (
                f"file body omitted from chat history ({n} chars already on disk). "
                "Do NOT re-file_write a history stub. "
                "file_read the path if you need the source; else file_edit or write NEW code."
            )
        return out

    if name in _EDIT_BODY_TOOLS:
        # Single-hunk fields
        for key in ("old_string", "new_string", "old", "new", "patch"):
            val = out.get(key)
            if isinstance(val, str) and len(val) > TOOL_ARGS_VALUE_MAX:
                out[key] = _summarize_large_string(val, kind=key)
                out["_history_summarized"] = True
        # Multi-hunk / batch: edits may be a JSON string or list
        edits = out.get("edits")
        if isinstance(edits, str) and len(edits) > TOOL_ARGS_VALUE_MAX:
            out["edits"] = _summarize_large_string(edits, kind="edits")
            out["_history_summarized"] = True
        elif isinstance(edits, list):
            slim: list[Any] = []
            for item in edits[:40]:
                if not isinstance(item, dict):
                    slim.append(item)
                    continue
                row = dict(item)
                for key in ("old_string", "new_string", "old", "new"):
                    val = row.get(key)
                    if isinstance(val, str) and len(val) > 400:
                        row[key] = _summarize_large_string(val, kind=key)
                        out["_history_summarized"] = True
                slim.append(row)
            out["edits"] = slim
        return out

    if name in _COMPUTER_TYPE_TOOLS:
        # Typed payloads (login passwords, tokens) must not re-enter provider history.
        for key in ("type", "type_text", "text", "password", "passwd"):
            val = out.get(key)
            if not isinstance(val, str) or not val:
                continue
            # computer_act uses "click" for labels — only redact type-like fields.
            # computer_type uses "text" as the payload.
            if name == "computer_act" and key == "text":
                continue
            scrubbed = _scrub_text(val, max_len=len(val) + 32)
            secretish = scrubbed != val or _looks_like_secret_payload(val)
            # password/passwd keys always redacted; type/text only when secret-like
            if key in ("password", "passwd") or secretish:
                out[key] = f"<<redacted typed input chars={len(val)}>>"
                out["_history_summarized"] = True
        return out

    return out


def _looks_like_secret_payload(value: str) -> bool:
    """Heuristic: password-like single tokens without spaces."""
    t = (value or "").strip()
    if len(t) < 8 or " " in t or "\n" in t:
        return False
    # Mixed class or long opaque token
    has_alpha = any(c.isalpha() for c in t)
    has_digit = any(c.isdigit() for c in t)
    has_special = any(not c.isalnum() for c in t)
    return (has_alpha and has_digit) or has_special or len(t) >= 20


def _scrub_tool_args_obj(obj: Any, *, depth: int = 0) -> Any:
    """Scrub secrets and cap nested string values without breaking JSON shape."""
    if depth > 14:
        return "[truncated]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                out[ks] = "[redacted]"
            else:
                out[ks] = _scrub_tool_args_obj(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_scrub_tool_args_obj(x, depth=depth + 1) for x in obj[:200]]
    if isinstance(obj, str):
        # Reuse full scrub (metabolism patterns + fallback) then value cap.
        t = _scrub_text(obj, max_len=TOOL_ARGS_VALUE_MAX)
        return t
    return obj


def _unescape_json_string_fragment(fragment: str) -> str:
    """Decode a JSON string body that may be missing its closing quote."""
    try:
        return json.loads('"' + fragment + '"')
    except Exception:
        return (
            fragment.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _extract_path_from_tool_json(raw: str) -> str | None:
    """Pull path/file from truncated or partial tool-call JSON."""
    s = raw or ""
    m = re.search(
        r'"(?:path|file|filepath|filename|target)"\s*:\s*"((?:\\.|[^"\\])*)"',
        s,
    )
    if m:
        try:
            return str(json.loads(f'"{m.group(1)}"')).strip() or None
        except Exception:
            return m.group(1).replace("\\/", "/").replace("\\\\", "\\").strip() or None
    # Bare: path: src/foo.py  or  "src/core/app.py" alone
    m2 = re.search(
        r'(?i)(?:path|file)\s*[:=]\s*["\']?([A-Za-z]:[^"\s,}]+\.\w{1,8}|[\w./\\-]+\.\w{1,8})',
        s,
    )
    if m2:
        return m2.group(1).strip().strip("'\"") or None
    m3 = re.search(
        r'["\']((?:src|tests|docs|lib|app)[/\\][^"\']+\.\w{1,8})["\']',
        s,
    )
    if m3:
        return m3.group(1).strip() or None
    return None


def _try_repair_file_write_json(raw: str) -> dict[str, Any] | None:
    """Best-effort recover path+content from truncated file_write tool JSON.

    Local models often hit max_tokens mid-string inside ``content``. If we can
    still extract a path and a usable content prefix, write that rather than
    spinning HISTORY_STUB failures.
    """
    s = (raw or "").strip()
    path = _extract_path_from_tool_json(s)
    if not path:
        return None

    m_c = re.search(r'"content"\s*:\s*"', s)
    if not m_c:
        # path-only salvage (useful for file_read / list_dir after truncate)
        return {"path": path, "_repaired_truncated": True, "_path_only": True}
    body_raw = s[m_c.end() :]
    # Walk escapes to find closing quote (if any)
    i = 0
    out_chars: list[str] = []
    closed = False
    while i < len(body_raw):
        ch = body_raw[i]
        if ch == "\\" and i + 1 < len(body_raw):
            out_chars.append(body_raw[i : i + 2])
            i += 2
            continue
        if ch == '"':
            closed = True
            break
        out_chars.append(ch)
        i += 1
    fragment = "".join(out_chars)
    content = _unescape_json_string_fragment(fragment)
    if not isinstance(content, str) or len(content.strip()) < 1:
        return {"path": path, "_repaired_truncated": True, "_path_only": True}
    # Avoid writing near-empty stubs from 1-2 char truncations
    if len(content) < 8 and not closed:
        return {"path": path, "content": content, "_repaired_truncated": True, "_repaired_closed": False}
    return {
        "path": path.strip(),
        "content": content,
        "_repaired_truncated": True,
        "_repaired_closed": closed,
    }


def coerce_tool_arguments_json(args: Any, *, tool_name: str = "") -> str:
    """Return valid JSON for tool **execution** — full fidelity, no clipping.

    Used by :func:`normalize_tool_calls` before tools run. Incomplete stream
    blobs: repair path (+ content for writes); path-only is enough for reads.
    """
    if args is None:
        return "{}"
    if isinstance(args, (dict, list)):
        try:
            return json.dumps(args, default=str, ensure_ascii=False)
        except Exception:
            return "{}"
    s = str(args)
    if not s.strip():
        return "{}"
    try:
        # Round-trip only to confirm validity; keep original string when possible
        # so we do not re-order keys or re-escape unnecessarily.
        json.loads(s)
        return s
    except json.JSONDecodeError:
        repaired = _try_repair_file_write_json(s)
        tname = (tool_name or "").strip().lower()
        if repaired is not None:
            # file_read / list_dir: path alone is a complete, executable call
            if tname in (
                "file_read",
                "list_dir",
                "repo_search",
                "bash_exec",
            ) or repaired.get("_path_only"):
                if tname in ("file_read", "list_dir", "repo_search") or (
                    repaired.get("_path_only") and tname not in ("file_write", "file_edit")
                ):
                    slim = {"path": repaired["path"]}
                    if tname == "bash_exec" and "command" in s:
                        # leave invalid if no command — don't invent
                        pass
                    else:
                        try:
                            return json.dumps(slim, ensure_ascii=False)
                        except Exception:
                            pass
            if tname in ("file_write", "file_edit", "file_edit_batch", "") and not repaired.get(
                "_path_only"
            ):
                # Unclosed content string = mid-stream cut. Do not write a stump.
                if tname == "file_write" and repaired.get("_repaired_closed") is False:
                    pass
                else:
                    try:
                        return json.dumps(
                            {
                                k: v
                                for k, v in repaired.items()
                                if not str(k).startswith("_") or k in ("path", "content")
                            },
                            ensure_ascii=False,
                        )
                    except Exception:
                        pass
            # file_write with path but truncated content still salvageable
            if tname == "file_write" and repaired.get("content"):
                try:
                    return json.dumps(
                        {
                            "path": repaired["path"],
                            "content": repaired["content"],
                            "force_full_write": True,
                            "_repaired_truncated": True,
                        },
                        ensure_ascii=False,
                    )
                except Exception:
                    pass
            # Generic path-bearing tools: prefer path-only execute over hard fail
            if repaired.get("path") and tname not in (
                "file_write",
                "file_edit",
                "file_edit_batch",
            ):
                try:
                    return json.dumps({"path": repaired["path"]}, ensure_ascii=False)
                except Exception:
                    pass
        preview = s[:400]
        return json.dumps(
            {
                "_invalid_json": True,
                "_stream_truncated": True,
                "note": (
                    "tool arguments JSON was cut off mid-stream (output budget)."
                ),
                "preview": preview,
                "path": (_extract_path_from_tool_json(s) or ""),
            },
            ensure_ascii=False,
        )


def sanitize_tool_arguments(args: Any, *, tool_name: str = "") -> str:
    """Return valid JSON string for tool-call ``function.arguments`` (provider history).

    **Outbound / history only.** Never use this on the execute path — it
    summarizes large ``file_write`` bodies into history stubs and caps nested
    strings. Use :func:`coerce_tool_arguments_json` before running tools.

    Never mid-string-clips the raw arguments blob — that produces
    ``EOF while parsing a string`` HTTP 400s from providers (xAI/DeepSeek).

    For *file_write* / large edit tools, large bodies are **summarized** for
    history (not half-clipped) so the model does not think files were truncated.
    """
    if args is None:
        return "{}"
    if isinstance(args, (dict, list)):
        try:
            rewritten = _rewrite_write_tool_args(args, tool_name) if tool_name else args
            cleaned = _scrub_tool_args_obj(rewritten)
            return json.dumps(cleaned, default=str, ensure_ascii=False)
        except Exception:
            return "{}"
    s = str(args)
    if not s.strip():
        return "{}"
    # Prefer parse → scrub nested values → re-dump (always valid JSON).
    try:
        parsed = json.loads(s)
        if tool_name:
            parsed = _rewrite_write_tool_args(parsed, tool_name)
        cleaned = _scrub_tool_args_obj(parsed)
        out = json.dumps(cleaned, default=str, ensure_ascii=False)
        if len(out) > TOOL_ARGS_MAX:
            # Drop large nested strings harder, then hard-cap with still-valid JSON.
            cleaned = _scrub_tool_args_obj(parsed)  # already value-capped
            out = json.dumps(cleaned, default=str, ensure_ascii=False)
            if len(out) > TOOL_ARGS_MAX:
                return json.dumps(
                    {
                        "_truncated": True,
                        "note": "tool arguments exceeded size cap; summarized",
                        "preview": _scrub_text(s, max_len=800),
                    },
                    ensure_ascii=False,
                )
        return out
    except json.JSONDecodeError:
        # Incomplete/truncated stream args — never forward broken JSON upstream.
        preview = _scrub_text(s, max_len=400)
        return json.dumps(
            {
                "_invalid_json": True,
                "note": "tool arguments were truncated or invalid; use tool results instead",
                "preview": preview,
            },
            ensure_ascii=False,
        )


def sanitize_message(
    msg: dict[str, Any],
    *,
    privacy: bool | None = None,
    tool_content_max: int | None = None,
) -> dict[str, Any]:
    """Return a sanitized copy of one chat message (does not mutate input).

    Fast path: shallow copy + string scrub for common role/content messages.
    Deepcopy only when nested tool_calls or non-string content need isolation.

    *privacy*: when True, tighter tool caps + email/phone scrub. Default reads
    :func:`privacy_mode_enabled` once per call when None (still cheap).
    *tool_content_max*: override tool body cap (local/RMB uses a higher default).
    """
    if privacy is None:
        privacy = privacy_mode_enabled()
    if tool_content_max is not None:
        tool_max = int(tool_content_max)
    else:
        tool_max = TOOL_CONTENT_MAX_PRIVACY if privacy else TOOL_CONTENT_MAX
    text_max = TEXT_CONTENT_MAX_PRIVACY if privacy else TEXT_CONTENT_MAX

    if not isinstance(msg, dict):
        return {
            "role": "user",
            "content": _scrub_text(str(msg), max_len=text_max, privacy=privacy),
        }

    role = str(msg.get("role") or "")
    content = msg.get("content")
    has_tool_calls = isinstance(msg.get("tool_calls"), list) and bool(msg["tool_calls"])
    nested = has_tool_calls or not isinstance(content, (str, type(None)))

    if nested:
        m = copy.deepcopy(msg)
        content = m.get("content")
    else:
        m = dict(msg)

    if role in ("tool", "function"):
        tool_name = str(m.get("name") or "").strip().lower()
        cap = tool_max
        if privacy and tool_name in _PRIVACY_HEAVY_TOOLS:
            cap = min(cap, 1_800)
        if isinstance(content, str):
            m["content"] = _scrub_text(content, max_len=cap, privacy=privacy)
        elif content is not None:
            m["content"] = _scrub_obj(content)
    elif isinstance(content, str):
        m["content"] = _scrub_text(content, max_len=text_max, privacy=privacy)
    elif isinstance(content, list):
        # Multimodal parts
        parts = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(p)
                continue
            q = dict(p)
            if q.get("type") == "text" and isinstance(q.get("text"), str):
                q["text"] = _scrub_text(q["text"], max_len=text_max, privacy=privacy)
            # Do not forward raw base64 blobs larger than needed — leave image_url as-is
            # (vision path usually uses decode brief as text already).
            parts.append(q)
        m["content"] = parts
    elif content is not None:
        m["content"] = _scrub_obj(content)

    # Strip any accidental secret-bearing top-level keys
    for k in list(m.keys()):
        if _SECRET_KEY_RE.search(str(k)) and k not in (
            "role",
            "content",
            "tool_calls",
            "name",
            "tool_call_id",
            "reasoning_content",
        ):
            m[k] = "[redacted]"

    if "tool_calls" in m and isinstance(m["tool_calls"], list):
        cleaned = []
        for tc in m["tool_calls"]:
            if not isinstance(tc, dict):
                continue
            tcc = tc if nested else copy.deepcopy(tc)
            fn = tcc.get("function")
            if isinstance(fn, dict):
                tname = str(fn.get("name") or tcc.get("name") or "")
                fn["arguments"] = sanitize_tool_arguments(
                    fn.get("arguments"), tool_name=tname
                )
            cleaned.append(tcc)
        m["tool_calls"] = cleaned

    return m


def sanitize_messages_for_provider(
    messages: list[dict[str, Any]] | None,
    *,
    privacy: bool | None = None,
    tool_content_max: int | None = None,
) -> list[dict[str, Any]]:
    """Sanitize a full messages array for outbound provider HTTP.

    Resolves privacy mode **once** for the batch (config/env) so the hot path
    does not re-read config per message.
    """
    if privacy is None:
        privacy = privacy_mode_enabled()
    return [
        sanitize_message(m, privacy=privacy, tool_content_max=tool_content_max)
        for m in (messages or [])
        if isinstance(m, dict)
    ]


def sanitize_chat_body(
    body: dict[str, Any],
    *,
    privacy: bool | None = None,
    local_agent: bool = False,
    tool_content_max: int | None = None,
) -> dict[str, Any]:
    """Copy body and sanitize messages (does not mutate input).

    *local_agent*: raise tool body cap so RMB/local coding keeps file contents.
    """
    out = dict(body)
    cap = tool_content_max
    if cap is None and local_agent and privacy is not True:
        cap = TOOL_CONTENT_MAX_LOCAL
    if "messages" in out:
        out["messages"] = sanitize_messages_for_provider(
            out.get("messages"), privacy=privacy, tool_content_max=cap
        )
    return out

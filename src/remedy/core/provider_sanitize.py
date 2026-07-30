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
# Tool *call* arguments must stay valid JSON — never mid-string clip (providers
# return HTTP 400 "EOF while parsing a string at column ~6000" if we truncate).
TOOL_ARGS_MAX = 48_000
# Nested string values inside tool-call JSON (after parse).
TOOL_ARGS_VALUE_MAX = 8_000
# file_write content may be large; keep more for *in-turn* fidelity when small,
# but summarize past full-body dumps so history does not look half-written.
FILE_WRITE_CONTENT_HISTORY_MAX = 1_200
# Max for normal user/assistant text (already bounded in practice).
TEXT_CONTENT_MAX = 100_000

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


def _scrub_text(text: str, *, max_len: int) -> str:
    """Redact secret-shaped substrings then size-cap for provider HTTP.

    Prefer shared metabolism redaction (broader patterns: PEM, DB URLs,
    Discord webhooks, …) so outbound chat matches ledger/IR fail-closed policy.
    """
    raw = text or ""
    try:
        from remedy.core.metabolism.redact import redact_text

        t = redact_text(raw)
    except Exception:
        t = _SECRET_VALUE_RE.sub("[redacted]", raw)
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
        f"DO_NOT_file_write_this_string file_read_the_path_instead>>"
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
            out["content"] = _summarize_large_string(content, kind="file_write content")
            out["_content_chars"] = len(content)
            out["_history_summarized"] = True
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


def coerce_tool_arguments_json(args: Any) -> str:
    """Return valid JSON for tool **execution** — full fidelity, no clipping.

    Used by :func:`normalize_tool_calls` before ``file_write`` / ``file_edit``
    run. Must **not** summarize, mid-clip nested strings, or redact body text:
    those corrupt source files on disk (history-stub / 8k ellipsis bug).

    Incomplete stream blobs become ``{_invalid_json: true, ...}`` so callers
    can refuse instead of writing garbage.
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
        preview = s[:400]
        return json.dumps(
            {
                "_invalid_json": True,
                "note": "tool arguments were truncated or invalid; use tool results instead",
                "preview": preview,
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


def sanitize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of one chat message (does not mutate input).

    Fast path: shallow copy + string scrub for common role/content messages.
    Deepcopy only when nested tool_calls or non-string content need isolation.
    """
    if not isinstance(msg, dict):
        return {"role": "user", "content": _scrub_text(str(msg), max_len=TEXT_CONTENT_MAX)}

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
        if isinstance(content, str):
            m["content"] = _scrub_text(content, max_len=TOOL_CONTENT_MAX)
        elif content is not None:
            m["content"] = _scrub_obj(content)
    elif isinstance(content, str):
        m["content"] = _scrub_text(content, max_len=TEXT_CONTENT_MAX)
    elif isinstance(content, list):
        # Multimodal parts
        parts = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(p)
                continue
            q = dict(p)
            if q.get("type") == "text" and isinstance(q.get("text"), str):
                q["text"] = _scrub_text(q["text"], max_len=TEXT_CONTENT_MAX)
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
) -> list[dict[str, Any]]:
    """Sanitize a full messages array for outbound provider HTTP."""
    return [sanitize_message(m) for m in (messages or []) if isinstance(m, dict)]


def sanitize_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    """Copy body and sanitize messages (does not mutate input)."""
    out = dict(body)
    if "messages" in out:
        out["messages"] = sanitize_messages_for_provider(out.get("messages"))
    return out

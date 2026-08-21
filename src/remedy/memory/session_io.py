"""Plain-text session export / import (round-trippable .txt).

Export format (UTF-8)::

    # Remedy Session
    Title: My Chat
    Model: grok-3
    Agent: default
    Source-Session-ID: <uuid>
    Exported: 2026-07-24T12:00:00+00:00

    ===== USER =====
    Hello

    ===== ASSISTANT =====
    Hi there

Also parses legacy markdown exports (``# Title`` + ``**User**`` / ``---``)
and freeform text (whole file becomes one user message).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from remedy.memory.inline_images import has_inline_image, strip_inline_images

_ROLE_HEADER = re.compile(
    r"^=====\s*(USER|ASSISTANT|SYSTEM|TOOL)\s*=====\s*$",
    re.IGNORECASE,
)
_LEGACY_ROLE = re.compile(
    r"^\*\*(User|Assistant|System|Tool)\*\*"
    r"(?:\s*\(([^)]*)\))?"
    r"(?:\s*—\s*([^`]+))?"
    r"(?:\s*`([^`]+)`)?\s*$",
    re.IGNORECASE,
)
_META_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")


@dataclass
class ParsedMessage:
    role: str  # user | assistant | system | tool
    content: str
    model: str | None = None
    agent: str | None = None
    created_at: str | None = None


@dataclass
class ParsedSession:
    title: str = "Imported Session"
    model: str | None = None
    agent: str | None = None
    source_session_id: str | None = None
    messages: list[ParsedMessage] = field(default_factory=list)


def _role_value(role: Any) -> str:
    if role is None:
        return "user"
    if hasattr(role, "value"):
        return str(role.value).lower()
    return str(role).lower()


def _msg_field(m: Any, name: str, default: Any = None) -> Any:
    if isinstance(m, dict):
        return m.get(name, default)
    return getattr(m, name, default)


def safe_filename_stem(title: str, max_len: int = 60) -> str:
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in (title or "Session"))
    safe = safe.strip() or "Session"
    return safe[:max_len]


# Cap huge tool dumps / data-URIs so export does not freeze UI or fill RAM.
# User/assistant stay readable; tool payloads are the usual multi-MB freeze source.
_EXPORT_CONTENT_CAP = 48_000
_EXPORT_TOOL_CAP = 2_000
_EXPORT_MAX_MESSAGES = 2_000


def _export_content(
    content: str,
    *,
    cap: int = _EXPORT_CONTENT_CAP,
    role: str | None = None,
) -> str:
    if not content:
        return ""
    # Never write secret-shaped substrings into portable exports (API keys,
    # bearer tokens, PEM, connection strings). Prefer over-redact.
    try:
        from remedy.core.metabolism.redact import redact_text

        content = redact_text(content)
    except Exception:
        # Fail closed: if redactor is unavailable, strip common key prefixes.
        content = re.sub(
            r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)",
            "[redacted]",
            content,
        )
    role_l = (role or "").lower()
    if role_l == "tool":
        cap = min(cap, _EXPORT_TOOL_CAP)
        # Tool dumps are rarely useful in a portable .txt; keep a short stub.
        one_line = " ".join(content.split())
        if len(one_line) > cap:
            return one_line[:cap] + f"…[tool output truncated {len(one_line) - cap} chars]"
        return one_line

    # Fast path: already small and no data-URI marker.
    if len(content) <= cap and not has_inline_image(content):
        return content.rstrip("\n")

    # Strip inline base64 images (common in comfyui / previews) — keep a stub.
    cleaned = strip_inline_images(content, min_bytes=0, stub="[image omitted in export]")
    if len(cleaned) <= cap:
        return cleaned.rstrip("\n")
    return (
        cleaned[:cap].rstrip("\n")
        + f"\n\n…[export truncated {len(cleaned) - cap} chars]"
    )


def format_session_txt(
    *,
    title: str,
    session_id: str,
    messages: list[Any],
    model: str | None = None,
    agent: str | None = None,
) -> str:
    """Build a plain-text export for a chat session."""
    lines: list[str] = [
        "# Remedy Session",
        f"Title: {title or 'Session'}",
    ]
    if model:
        lines.append(f"Model: {model}")
    if agent:
        lines.append(f"Agent: {agent}")
    lines.append(f"Source-Session-ID: {session_id}")
    lines.append(f"Exported: {datetime.now(UTC).isoformat()}")
    total = len(messages)
    if total > _EXPORT_MAX_MESSAGES:
        lines.append(
            f"Messages: {total} (exporting last {_EXPORT_MAX_MESSAGES}; older omitted for size)"
        )
        messages = messages[-_EXPORT_MAX_MESSAGES:]
    else:
        lines.append(f"Messages: {total}")
    lines.append("")

    for m in messages:
        role = _role_value(_msg_field(m, "role", "user"))
        content = _msg_field(m, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"===== {role.upper()} =====")
        # Optional per-message metadata as comment-style lines (ignored if empty)
        m_agent = _msg_field(m, "agent")
        m_model = _msg_field(m, "model")
        created = _msg_field(m, "created_at")
        if created is not None and not isinstance(created, str):
            try:
                created = created.isoformat()
            except Exception:
                created = str(created)
        meta_bits: list[str] = []
        if m_agent:
            meta_bits.append(f"agent={m_agent}")
        if m_model:
            meta_bits.append(f"model={m_model}")
        if created:
            meta_bits.append(f"at={str(created)[:19]}")
        if meta_bits:
            lines.append(f"# {' | '.join(meta_bits)}")
        body = _export_content(content, role=role)
        if body:
            lines.append(body)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_session_markdown(
    *,
    title: str,
    session_id: str,
    messages: list[Any],
) -> str:
    """Legacy markdown export (still available via format=md)."""
    lines = [
        f"# {title or 'Session'}",
        "",
        f"**Session ID:** `{session_id}`",
        f"**Messages:** {len(messages)}",
        "",
    ]
    for m in messages:
        role = _role_value(_msg_field(m, "role", "user"))
        content = _msg_field(m, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        created = _msg_field(m, "created_at") or ""
        if created is not None and not isinstance(created, str):
            try:
                created = created.isoformat()
            except Exception:
                created = str(created)
        agent = _msg_field(m, "agent") or ""
        model = _msg_field(m, "model") or ""
        header = f"**{role.capitalize()}**"
        if agent:
            header += f" ({agent})"
        if model:
            header += f" — {model}"
        if created:
            header += f" `{str(created)[:19]}`"
        lines.append(header)
        lines.append("")
        body = _export_content(content, role=role)
        if body:
            lines.append(body)
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _parse_msg_meta_comment(line: str) -> dict[str, str]:
    """Parse ``# agent=x | model=y | at=...`` after a role header."""
    out: dict[str, str] = {}
    if not line.startswith("#"):
        return out
    body = line[1:].strip()
    for part in body.split("|"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_session_text(text: str) -> ParsedSession:
    """Parse export text into a session skeleton + messages."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        raise ValueError("Empty session text")

    lines = raw.split("\n")

    # New format: role markers or explicit header
    if any(_ROLE_HEADER.match(ln) for ln in lines) or (
        lines and lines[0].strip().lower().startswith("# remedy session")
    ):
        return _parse_native(lines)

    # Legacy markdown: **User** / **Assistant** blocks
    if any(_LEGACY_ROLE.match(ln.strip()) for ln in lines):
        return _parse_legacy_markdown(lines)

    # Freeform: first # heading as title, rest as one user message
    title = "Imported Session"
    body_start = 0
    first = lines[0].strip()
    if first.startswith("#"):
        title = first.lstrip("#").strip() or title
        body_start = 1
        while body_start < len(lines) and not lines[body_start].strip():
            body_start += 1
    content = "\n".join(lines[body_start:]).strip()
    if not content:
        raise ValueError("No message content found in file")
    return ParsedSession(
        title=title,
        messages=[ParsedMessage(role="user", content=content)],
    )


def _parse_native(lines: list[str]) -> ParsedSession:
    session = ParsedSession()
    i = 0
    # Header / meta until first role marker
    while i < len(lines):
        ln = lines[i]
        if _ROLE_HEADER.match(ln.strip()):
            break
        stripped = ln.strip()
        if stripped.startswith("#") and "remedy session" in stripped.lower():
            i += 1
            continue
        if stripped.startswith("#"):
            # allow "# Title" as alternate title
            if session.title == "Imported Session":
                t = stripped.lstrip("#").strip()
                if t and not t.lower().startswith("remedy"):
                    session.title = t
            i += 1
            continue
        m = _META_LINE.match(stripped)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if key == "title" and val:
                session.title = val
            elif key == "model" and val:
                session.model = val
            elif key == "agent" and val:
                session.agent = val
            elif key in ("source-session-id", "session-id", "session_id") and val:
                session.source_session_id = val
            # Messages: count ignored
        i += 1

    current_role: str | None = None
    current_meta: dict[str, str] = {}
    buf: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_meta, buf
        if current_role is None:
            buf = []
            return
        content = "\n".join(buf).strip("\n")
        # Keep content even if empty? skip empty assistant/user
        if content.strip() or current_role in ("user", "assistant"):
            session.messages.append(
                ParsedMessage(
                    role=current_role,
                    content=content.strip(),
                    model=current_meta.get("model") or session.model,
                    agent=current_meta.get("agent") or session.agent,
                    created_at=current_meta.get("at"),
                )
            )
        current_role = None
        current_meta = {}
        buf = []

    while i < len(lines):
        ln = lines[i]
        rm = _ROLE_HEADER.match(ln.strip())
        if rm:
            flush()
            current_role = rm.group(1).lower()
            current_meta = {}
            # peek next line for meta comment
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("#"):
                peek = lines[i + 1].strip()
                if "=" in peek:
                    current_meta = _parse_msg_meta_comment(peek)
                    i += 2
                    continue
            i += 1
            continue
        if current_role is not None:
            buf.append(ln)
        i += 1
    flush()

    if not session.messages:
        raise ValueError("No messages found in Remedy session text")
    return session


def _parse_legacy_markdown(lines: list[str]) -> ParsedSession:
    session = ParsedSession()
    i = 0
    if lines and lines[0].strip().startswith("#"):
        session.title = lines[0].strip().lstrip("#").strip() or session.title
        i = 1

    # Skip preamble until first **Role**
    while i < len(lines) and not _LEGACY_ROLE.match(lines[i].strip()):
        stripped = lines[i].strip()
        sid = re.search(r"\*\*Session ID:\*\*\s*`([^`]+)`", stripped)
        if sid:
            session.source_session_id = sid.group(1)
        i += 1

    current_role: str | None = None
    current_agent: str | None = None
    current_model: str | None = None
    current_at: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_agent, current_model, current_at, buf
        if current_role is None:
            buf = []
            return
        # Drop trailing --- separators already handled by not including them
        content = "\n".join(buf).strip()
        # strip trailing --- if left in buffer
        if content.endswith("---"):
            content = content[: -3].rstrip()
        session.messages.append(
            ParsedMessage(
                role=current_role,
                content=content,
                model=current_model,
                agent=current_agent,
                created_at=current_at,
            )
        )
        current_role = None
        current_agent = None
        current_model = None
        current_at = None
        buf = []

    while i < len(lines):
        stripped = lines[i].strip()
        rm = _LEGACY_ROLE.match(stripped)
        if rm:
            flush()
            current_role = rm.group(1).lower()
            current_agent = (rm.group(2) or "").strip() or None
            current_model = (rm.group(3) or "").strip() or None
            current_at = (rm.group(4) or "").strip() or None
            i += 1
            # skip blank after header
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        if stripped == "---" and current_role is not None:
            flush()
            i += 1
            continue
        if current_role is not None:
            buf.append(lines[i])
        i += 1
    flush()

    if not session.messages:
        raise ValueError("No messages found in markdown session export")
    return session

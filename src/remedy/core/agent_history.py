"""Session chat history loading for multi-turn continuity."""

from __future__ import annotations

import logging
from typing import Any

from remedy.core.react_policy import (
    HISTORY_CHAR_BUDGET,
    HISTORY_MSG_LIMIT,
    HISTORY_MSG_SOFT_TRIM,
)
from remedy.models import ChatMessageRole

logger = logging.getLogger(__name__)


async def load_session_history(
    memory: Any,
    session_id: str | None,
    current_user: str,
    *,
    msg_limit: int = HISTORY_MSG_LIMIT,
    char_budget: int = HISTORY_CHAR_BUDGET,
    soft_trim: int = HISTORY_MSG_SOFT_TRIM,
) -> list[dict[str, Any]]:
    """Load recent user/assistant turns for multi-turn continuity."""
    if not session_id or memory is None:
        return []
    try:
        rows = await memory.get_chat_messages(session_id, limit=msg_limit)
    except Exception:
        logger.debug("session history load failed", exc_info=True)
        return []

    # Drop trailing user message if API already persisted the current turn.
    if rows and rows[-1].role == ChatMessageRole.USER:
        last = (rows[-1].content or "").strip()
        if last == (current_user or "").strip():
            rows = rows[:-1]

    budget = char_budget
    # Walk newest→oldest then reverse so we keep the most recent context.
    selected: list[dict[str, Any]] = []
    for msg in reversed(rows):
        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        if role not in ("user", "assistant"):
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        # Strip internal tool markers from prior assistant bubbles.
        if role == "assistant":
            if content.startswith("@@") or "[LLM" in content[:40]:
                continue
            # Soft-trim only when explicitly configured (>0). Default 0 = full text.
            if soft_trim > 0 and len(content) > soft_trim:
                content = content[:soft_trim] + "\n…[truncated]"
        # Prefer dropping older turns over mid-message slicing.
        if len(content) > budget:
            if selected:
                break
            # Newest message alone exceeds budget — keep full unless soft-trim on.
            if soft_trim > 0:
                content = content[:budget] + "\n…[truncated]"
        budget -= len(content)
        selected.append({"role": role, "content": content})
        if budget <= 0:
            break
    selected.reverse()
    return selected

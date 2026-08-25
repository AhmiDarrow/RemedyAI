"""Session title helpers for stream routes."""

from __future__ import annotations

import re


def looks_like_path_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    if t.startswith("\\\\") or t.startswith("/Users/") or t.startswith("/home/"):
        return True
    if "\\" in t and re.search(
        r"\.(png|jpe?g|gif|webp|bmp|heic|pdf|docx?)$", t, re.I
    ):
        return True
    return bool(
        re.match(r"^Screenshot\b", t, re.I)
        and re.search(r"\.(png|jpe?g|gif|webp)$", t, re.I)
    )


def title_from_attachment_name(name: str, *, max_len: int = 52) -> str:
    raw = (name or "").strip().replace("/", "\\")
    if not raw:
        return "Attachment"
    base = raw.rsplit("\\", 1)[-1]
    pretty = re.sub(r"\.(png|jpe?g|gif|webp|bmp|heic)$", "", base, flags=re.I)
    t = re.sub(r"[_-]+", " ", pretty)
    t = " ".join(t.split()).strip() or "Image"
    if re.match(r"^Screenshot\b", t, re.I):
        t = re.sub(r"\s+\d{4}.*$", "", t).strip() or "Screenshot"
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t


_LIVING_TITLE_SKIP = frozenset(
    {
        "ok",
        "k",
        "yes",
        "y",
        "no",
        "n",
        "continue",
        "thanks",
        "thank you",
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "stop",
        "wait",
        "got it",
        "cool",
        "nice",
    }
)


def should_refresh_living_title(current: str, prompt: str) -> bool:
    """Endless sessions keep one thread; the sidebar name follows the latest beat.

    Skip acks / continue / greetings so 'ok' does not retitle a long chat.
    """
    t = " ".join((prompt or "").strip().split())
    if not t or looks_like_path_title(t):
        return False
    low = t.lower().rstrip("!.?")
    if low in _LIVING_TITLE_SKIP:
        return False
    if len(t) < 16 and "?" not in t:
        return False
    cur = (current or "").strip()
    if not cur:
        return True
    if cur.lower() in (
        "new session",
        "new chat",
        "untitled",
        "attachments",
        "attachment",
        "image",
        "screenshot",
    ):
        return True
    new = title_from_prompt(t)
    return bool(new) and new.casefold() != cur.casefold()


def title_from_prompt(
    text: str,
    *,
    max_len: int = 52,
    att_dicts: list | None = None,
) -> str:
    t = " ".join((text or "").strip().split())
    if not t:
        return "New Session"
    # Drop attachment display blocks from title.
    if "📎" in t:
        t = t.split("📎", 1)[0].strip() or t
    if t.startswith("(") and "see attached" in t.lower():
        name = (
            (att_dicts[0].get("name") if att_dicts else "") or "Attachments"
        )
        t = title_from_attachment_name(str(name), max_len=max_len)
    elif looks_like_path_title(t):
        t = title_from_attachment_name(t, max_len=max_len)
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t or "New Session"

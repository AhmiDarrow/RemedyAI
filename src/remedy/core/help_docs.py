"""Owner's manual / F1 help — always available to the agent (read-only).

The desktop Help wiki (F1) is the same markdown as ``docs/manual/``. When the
user's project is not the RemedyAI repo, ``file_read`` can fail path-jail and
the model incorrectly claims help is "outside access scope."

This module resolves help roots and loads articles by id without depending on
project scope. Mutations are never exposed.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Safe article id: slug, no path traversal
_ARTICLE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}$")


def _candidate_help_dirs() -> list[Path]:
    """Ordered search paths for manual markdown (first hit wins per article)."""
    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None) -> None:
        if p is None:
            return
        try:
            r = p.expanduser().resolve()
        except OSError:
            r = p.expanduser().absolute()
        key = str(r).lower()
        if key in seen:
            return
        if r.is_dir():
            seen.add(key)
            out.append(r)

    # Dev / editable install: repo layout
    env_root = (os.environ.get("REMEDY_DEV_ROOT") or "").strip()
    if env_root:
        root = Path(env_root)
        _add(root / "docs" / "manual")
        _add(root / "desktop" / "src" / "help" / "articles")

    # Package-relative: .../RemedyAI/src/remedy/core/help_docs.py → repo root
    here = Path(__file__).resolve()
    for parent in here.parents:
        _add(parent / "docs" / "manual")
        _add(parent / "desktop" / "src" / "help" / "articles")
        # Stop at drive root after a few levels
        if parent.parent == parent:
            break

    # Optional staged copy for packaged installs
    home = Path(os.environ.get("REMEDY_HOME") or Path.home() / ".remedy")
    _add(home / "help" / "manual")
    _add(home / "help" / "articles")

    return out


@lru_cache(maxsize=1)
def help_read_roots() -> tuple[Path, ...]:
    """Read-only roots to fold into tool path resolution."""
    return tuple(_candidate_help_dirs())


def list_help_articles() -> list[dict[str, str]]:
    """Return ``{id, title, path}`` for every ``*.md`` under help roots."""
    found: dict[str, dict[str, str]] = {}
    for root in help_read_roots():
        try:
            for path in sorted(root.glob("*.md")):
                aid = path.stem
                if aid.lower() == "readme":
                    continue
                if aid in found:
                    continue
                title = aid
                try:
                    head = path.read_text(encoding="utf-8", errors="replace")[:400]
                    for line in head.splitlines():
                        s = line.strip()
                        if s.startswith("# "):
                            title = s[2:].strip() or aid
                            break
                except OSError:
                    pass
                found[aid] = {
                    "id": aid,
                    "title": title,
                    "path": str(path),
                }
        except OSError:
            continue
    # Stable order: numeric prefix first, then alpha
    def _key(item: dict[str, str]) -> tuple[int, str]:
        m = re.match(r"^(\d+)", item["id"])
        return (int(m.group(1)) if m else 999, item["id"].lower())

    return sorted(found.values(), key=_key)


def resolve_help_article(article_id: str) -> Path | None:
    """Resolve an article id or filename to a path, or None."""
    raw = (article_id or "").strip()
    if not raw:
        return None
    # Strip wiki-style prefixes
    raw = raw.replace("\\", "/").split("/")[-1]
    if raw.lower().endswith(".md"):
        stem = raw[:-3]
    else:
        stem = raw
    if not _ARTICLE_ID_RE.match(stem):
        return None
    name = f"{stem}.md"
    for root in help_read_roots():
        cand = root / name
        if cand.is_file():
            return cand
    return None


def read_help_article(article_id: str, *, max_chars: int = 48_000) -> dict[str, Any]:
    """Load one F1 / owner's manual article.

    Returns a public dict: ok, id, title, path, content | error.
    """
    path = resolve_help_article(article_id)
    if path is None:
        ids = [a["id"] for a in list_help_articles()]
        sample = ", ".join(ids[:24])
        more = f" (+{len(ids) - 24} more)" if len(ids) > 24 else ""
        return {
            "ok": False,
            "error": (
                f"Unknown help article {article_id!r}. "
                f"Use help_list for ids. Examples: {sample}{more}"
            ),
            "id": (article_id or "").strip(),
        }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {
            "ok": False,
            "error": f"Failed to read help article: {e}",
            "id": path.stem,
            "path": str(path),
        }
    title = path.stem
    for line in text.splitlines()[:20]:
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip() or title
            break
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated at {max_chars} chars]"
    return {
        "ok": True,
        "id": path.stem,
        "title": title,
        "path": str(path),
        "content": text,
    }


def clear_help_docs_cache() -> None:
    """Test helper — drop cached roots."""
    help_read_roots.cache_clear()

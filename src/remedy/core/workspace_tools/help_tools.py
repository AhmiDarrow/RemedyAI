"""Workspace tools — help."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.core.workspace_tools.guards import (
    junk_write_guard,
    note_path,
    parent_hint,
    reserved_guard,
    track_read,
)


def register_help_tools(runtime: Any) -> None:
    """Register help workspace tools."""
    def _parent_hint(path: str) -> str:
        return parent_hint(path)

    def _reserved_guard(path: str) -> str | None:
        return reserved_guard(path)

    def _junk_write_guard(path: str) -> str | None:
        return junk_write_guard(path)

    def _note_path(target: Path) -> None:
        note_path(runtime, target)

    def _track_read(target: Path) -> None:
        track_read(runtime, target)

    async def help_list() -> str:
        """List F1 / owner's manual articles (always available)."""
        from remedy.core.help_docs import list_help_articles

        arts = list_help_articles()
        if not arts:
            return (
                "No help articles found on disk. Dev: set REMEDY_DEV_ROOT to the "
                "repo root so docs/manual is discoverable."
            )
        lines = [
            "F1 Help / owner's manual (read with help_read(id=…)):",
            "",
        ]
        for a in arts:
            lines.append(f"- **{a['id']}** — {a['title']}")
        lines.append("")
        lines.append(
            "These are the same chapters as in-app F1. Always readable; "
            "not limited by project access scope."
        )
        return "\n".join(lines)

    async def help_read(id: str = "", article_id: str = "") -> str:
        """Read one F1 help article by id (e.g. computer-use-soak, 19-metabolism)."""
        from remedy.core.help_docs import read_help_article

        aid = (id or article_id or "").strip()
        if not aid:
            return (
                "help_read requires id= (article slug). Call help_list first. "
                "Example: help_read(id=\"computer-use-soak\")"
            )
        result = read_help_article(aid)
        if not result.get("ok"):
            return str(result.get("error") or "help_read failed")
        title = result.get("title") or result.get("id")
        path = result.get("path") or ""
        body = result.get("content") or ""
        return f"# {title}\n\n_Source: {path}_\n\n{body}"

    runtime.tool_registry.register_builtin_handler(
        "help_list",
        "List F1 Help / owner's manual article ids (same as in-app F1). "
        "Always available — not limited by project access scope. "
        "Then help_read(id=…) for full text.",
        help_list,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "help_read",
        "Read one F1 Help / owner's manual article by id "
        "(e.g. computer-use-soak, 00-overview, 19-metabolism, 18-agency). "
        "Always available read-only — never claim help is outside access scope.",
        help_read,
        {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Article id/slug (from help_list), e.g. computer-use-soak",
                },
                "article_id": {
                    "type": "string",
                    "description": "Alias for id",
                },
            },
            "required": ["id"],
        },
    )


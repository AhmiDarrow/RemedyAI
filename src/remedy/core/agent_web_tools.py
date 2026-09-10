"""Agent registration for web_fetch / web_search.

Implementation lives in ``remedy.core.web_helpers`` so the RMDY worker can
import search/fetch without the agent_* registration path.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError

from remedy.core.errors import format_tool_error
from remedy.core.web_helpers import (
    SearchConsentError,
    _host_is_blocked,
    _pinned_fetch,
    _rail_page_text,
    _rail_url_matches,
    _resolve_public_ips,
    _web_enabled,
    parse_ddg_html_results,
    polite_fetch,
    run_search,
    search_public_web,
    web_tools_enabled,
)

__all__ = [
    "SearchConsentError",
    "_host_is_blocked",
    "_pinned_fetch",
    "_rail_page_text",
    "_rail_url_matches",
    "_resolve_public_ips",
    "_web_enabled",
    "parse_ddg_html_results",
    "polite_fetch",
    "register_web_tools",
    "run_search",
    "search_public_web",
    "web_tools_enabled",
]


def register_web_tools(runtime: Any) -> None:
    """Register web_fetch + web_search (on unless the owner turned them off)."""

    def _web_disabled_msg(tool_name: str) -> str:
        return format_tool_error(
            "Web tools are off. The owner turned them off — ask before turning "
            f"them back on, then retry {tool_name}.",
            code="WEB_DISABLED",
            tool_name=tool_name,
            suggestion=(
                "If the owner wants them on: update_settings(web_tools_enabled=true)."
            ),
        )

    def _map_fetch_error(e: BaseException, *, tool_name: str) -> str:
        if isinstance(e, ValueError):
            msg = str(e)
            if msg == "ABORTED":
                return format_tool_error(
                    "Aborted by user",
                    code="ABORTED",
                    tool_name=tool_name,
                )
            if "USERINFO" in msg:
                return format_tool_error(
                    "Refused: URLs must not include user:password@ credentials.",
                    code="URL_USERINFO_BLOCKED",
                    tool_name=tool_name,
                    suggestion="Pass a plain https URL without embedded credentials.",
                )
            if "SSRF" in msg:
                return format_tool_error(
                    "Refused: private/localhost/metadata URLs are blocked (SSRF protection).",
                    code="SSRF_BLOCKED",
                    tool_name=tool_name,
                    suggestion="Use a public https URL, or read local files with read.",
                )
            if msg.startswith("ROBOTS_BLOCKED"):
                host = msg.split(" ", 1)[-1]
                return format_tool_error(
                    f"Skipped: {host} disallows automated clients on this path in robots.txt.",
                    code="ROBOTS_BLOCKED",
                    tool_name=tool_name,
                    suggestion=(
                        "Tell the owner the site asks bots not to read that page. They can "
                        "open it themselves, or set web_respect_robots=false to override."
                    ),
                )
            if msg.startswith("ROBOTS_DELAY"):
                return format_tool_error(
                    f"Skipped: {msg.split(' ', 1)[-1]}, which is longer than a turn should wait.",
                    code="ROBOTS_DELAY",
                    tool_name=tool_name,
                    suggestion="Fetch a different host now and come back to this one later.",
                )
            return format_tool_error(msg, code="BAD_URL", tool_name=tool_name)
        if isinstance(e, HTTPError):
            return format_tool_error(
                f"HTTP {e.code}: {e.reason}",
                code="HTTP_ERROR",
                tool_name=tool_name,
            )
        if isinstance(e, URLError):
            return format_tool_error(
                f"Network error: {getattr(e, 'reason', e)}",
                code="NETWORK_ERROR",
                tool_name=tool_name,
            )
        return format_tool_error(str(e), code="FETCH_ERROR", tool_name=tool_name)

    async def web_fetch(url: str = "", max_chars: int = 50_000) -> str:
        """Fetch a URL as readable text (opt-in web tools)."""
        if not _web_enabled(runtime):
            return _web_disabled_msg("web_fetch")
        u = (url or "").strip()
        if not u.startswith(("http://", "https://")):
            return format_tool_error(
                "url must start with http:// or https://",
                code="BAD_URL",
                tool_name="web_fetch",
            )
        try:
            cap = max(1000, min(200_000, int(max_chars or 50_000)))
        except (TypeError, ValueError):
            cap = 50_000
        try:
            final_url, raw, charset = polite_fetch(
                u, max_chars=max(cap * 4, 200_000), timeout=25.0, runtime=runtime
            )
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_fetch")

        shown = final_url if final_url != u else u
        text = raw.decode(charset or "utf-8", errors="replace")
        with suppress(Exception):
            from remedy.core.turn_context import current_turn_id, turn_session_id
            from remedy.memory.provenance import ingest_web_text

            ingest_web_text(
                text[:800],
                session_id=str(turn_session_id(runtime) or ""),
                turn_id=str(current_turn_id() or ""),
            )
        from remedy.core.html_extract import html_to_markdown, looks_like_html

        if looks_like_html(raw):
            extracted = html_to_markdown(text, max_chars=cap)
            body = str(extracted.get("markdown") or "").strip()
            # A thin extract is worth a second look, but the browser only
            # wins when it actually returns more than the HTTP extract did
            # — a short page is not the same thing as an empty shell.
            shell = bool(extracted.get("js_shell")) or not body
            if shell or len(body) < 80:
                rail = await _rail_page_text(u)
                if rail and (shell or len(rail) > len(body)):
                    why = (
                        "HTTP body was empty or script-only"
                        if shell
                        else "HTTP extract was thin"
                    )
                    return (
                        f"URL: {shown}\nSource: in-app browser ({why})\n\n"
                        f"{rail[:cap]}"
                    )
            if body:
                title = str(extracted.get("title") or "").strip()
                head = f"URL: {shown}"
                if title:
                    head += f"\nTitle: {title}"
                return f"{head}\n\n{body}"
            rail = await _rail_page_text(u)
            if rail:
                return (
                    f"URL: {shown}\nSource: in-app browser\n\n{rail[:cap]}"
                )
        if len(text) > cap:
            text = text[:cap] + f"\n…[truncated at {cap} chars]"
        return f"URL: {shown}\n\n{text}"

    async def web_search(query: str = "", max_results: float = 5.0) -> str:
        """Search the public web. Local OpenSERP if ready, else DuckDuckGo HTML."""
        if not _web_enabled(runtime):
            return _web_disabled_msg("web_search")
        q = (query or "").strip()
        if not q:
            return format_tool_error(
                "query is required",
                code="MISSING_QUERY",
                tool_name="web_search",
                suggestion='web_search(query="site:docs.python.org asyncio gather")',
            )
        if len(q) > 400:
            q = q[:400]
        try:
            n = int(max_results if max_results is not None else 5)
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(10, n))
        try:
            rows, backend = run_search(q, max_results=n, timeout=20.0, runtime=runtime)
        except SearchConsentError as consent:
            return format_tool_error(
                str(consent),
                code="SEARCH_CONSENT_REQUIRED",
                tool_name="web_search",
            )
        except Exception as e:
            return _map_fetch_error(e, tool_name="web_search")
        if not rows:
            return (
                f"Search: {q}\n\nNo structured results parsed. "
                "Try a simpler query, or web_fetch a known docs URL."
            )
        lines = [f"Search: {q}", f"Source: {backend}", f"Results: {len(rows)}", ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")
        lines.append(
            "Use web_fetch on promising URLs for full page text. "
            "Private/localhost hosts remain blocked (SSRF)."
        )
        return "\n".join(lines).strip()

    runtime.tool_registry.register_builtin_handler(
        "web_fetch",
        "Fetch an HTTP(S) URL as readable page text. "
        "HTML is stripped to markdown. Script-only pages fall back to the in-app browser. "
        "Private/localhost hosts are blocked (SSRF). Off only if the owner disabled web tools.",
        web_fetch,
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 50000)",
                    "default": 50000,
                },
            },
            "required": ["url"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "web_search",
        "Search the public web. Local OpenSERP (downloaded on first run) or "
        "DuckDuckGo HTML. Returns titles, URLs, and snippets; follow up with "
        "web_fetch for full pages. Private/localhost hosts stay blocked (SSRF).",
        web_search,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (keywords or site: filter)",
                },
                "max_results": {
                    "type": "number",
                    "description": "Max results to return (default 5, max 10)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )

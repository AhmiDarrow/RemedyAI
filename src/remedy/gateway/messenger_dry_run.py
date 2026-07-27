"""Local dry-run for messengers without Discord/Slack/etc. installed.

Simulates inbound traffic through GatewayEvent → session_bridge (same path as
live adapters). Does **not** call external platform APIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def dry_run_inbound(
    *,
    channel: str = "telegram",
    chat_id: str = "dry-run-chat",
    message: str = "dry-run hello from Remedy",
    username: str = "dry-run-user",
    home: Path | str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Inject one fake inbound message; return session/message proof."""
    from remedy.core.agent import BasicRuntime
    from remedy.gateway.channels.emit_util import emit_message
    from remedy.gateway.messengers import external_session_id
    from remedy.gateway.router import Gateway
    from remedy.gateway.session_bridge import handle_messenger_event
    from remedy.interfaces.api_support import load_config
    from remedy.memory.store import MemoryStore
    from remedy.models import AgentConfig, ChannelKind

    home_path = Path(home or Path.home() / ".remedy").expanduser()
    db = Path(db_path) if db_path else home_path / "memory.db"
    cfg = load_config() or {}

    try:
        kind = ChannelKind(str(channel).strip().lower())
    except ValueError:
        return {"ok": False, "error": f"unknown channel {channel!r}"}

    memory = MemoryStore(db)
    await memory.initialize()

    agent_cfg = AgentConfig(
        home_dir=str(home_path),
        memory_db_path=str(db),
        name=str(cfg.get("name") or "Remedy"),
        llm_provider=str(cfg.get("llm_provider") or "openai"),
        llm_model=str(cfg.get("llm_model") or "gpt-4o-mini"),
        llm_base_url=str(cfg.get("llm_base_url") or "https://api.openai.com/v1"),
    )
    try:
        from remedy.interfaces.config import resolve_provider_api_key

        key = resolve_provider_api_key(cfg, agent_cfg.llm_provider, home=home_path)
        if key:
            agent_cfg.llm_api_key = key
    except Exception:
        pass

    runtime = BasicRuntime(agent_cfg, memory=memory)
    await runtime.start()

    replies: list[str] = []
    gw = Gateway(runtime=runtime, memory_store=memory, rate_limit=9999)

    async def _handler(event):
        async for chunk in handle_messenger_event(runtime, event):
            if chunk is not None:
                replies.append(str(chunk))
                yield chunk

    gw.register_handler(_handler)

    try:
        await emit_message(
            gw,
            kind,
            message=message,
            chat_id=chat_id,
            source_id=username,
            username=username,
        )
        sid = external_session_id(kind.value, chat_id)
        session = await memory.get_chat_session(sid)
        msgs = await memory.get_chat_messages(sid, limit=20) if session else []
        return {
            "ok": bool(session) and len(msgs) >= 1,
            "channel": kind.value,
            "session_id": sid if session else None,
            "session_title": getattr(session, "title", None),
            "origin_channel": getattr(session, "origin_channel", None),
            "message_count": len(msgs),
            "roles": [
                m.role.value if hasattr(m.role, "value") else str(m.role) for m in msgs
            ],
            "reply_chars": sum(len(r) for r in replies),
            "db": str(db),
            "note": (
                "Local dry-run only — no Telegram/Discord/Slack network calls. "
                "If db is ~/.remedy/memory.db, refresh desktop Sessions to see it."
            ),
        }
    finally:
        await runtime.stop()
        await memory.close()


async def dry_run_all_channels(
    *,
    home: Path | str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run a short dry-run for each catalog messenger id."""
    from remedy.gateway.messengers import messenger_ids

    results = []
    for mid in messenger_ids():
        r = await dry_run_inbound(
            channel=mid,
            chat_id=f"dry-{mid}",
            message=f"dry-run via {mid}",
            username="dry-run",
            home=home,
            db_path=db_path,
        )
        results.append(r)
    ok = all(x.get("ok") for x in results)
    return {"ok": ok, "results": results}


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Simulate messenger inbound without external apps"
    )
    p.add_argument("--channel", default="telegram", help="Channel id or 'all'")
    p.add_argument("--chat-id", default="dry-run-chat")
    p.add_argument("--message", default="dry-run hello from Remedy")
    p.add_argument("--username", default="dry-run-user")
    p.add_argument(
        "--tmp-db",
        action="store_true",
        help="Use a temp memory.db (does not touch desktop sessions)",
    )
    ns = p.parse_args()

    db_path = None
    if ns.tmp_db:
        import tempfile

        db_path = Path(tempfile.mkdtemp()) / "memory.db"

    if ns.channel.strip().lower() == "all":
        result = asyncio.run(dry_run_all_channels(db_path=db_path))
    else:
        result = asyncio.run(
            dry_run_inbound(
                channel=ns.channel,
                chat_id=ns.chat_id,
                message=ns.message,
                username=ns.username,
                db_path=db_path,
            )
        )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

"""Mother-side hive glue: spawn-and-continue, evidence, coordination.

Daughters never speak to the owner. After a hire the mother keeps working
(Anthropic: waiting on a child dumps the whole transcript into the parent).
Packets land on the mother's evidence ledger. Each daughter publishes a
coordination beacon so two pulses cannot overwrite the same file.
"""

from __future__ import annotations

from typing import Any

from remedy.core.hive.types import CADENCE_POST, HiveDaughter, ReturnPacket

SPAWN_CONTINUE_HINT = (
    "A hive daughter is working independently. Continue your own work now. "
    "Do not wait for her packet. Do not mention the hire to the owner unless they asked. "
    "Call hive_collect later for the compact packet."
)

_HIRE_TOOLS = frozenset({"hive_spawn", "hive_assign"})


def _home_of(runtime: Any) -> str | None:
    return getattr(getattr(runtime, "config", None), "home_dir", None)


def announce_daughter(daughter: HiveDaughter, runtime: Any = None) -> None:
    """Publish a coordination beacon for this daughter's isolated session."""
    from remedy.core.coordination import register

    register(
        daughter.session_id,
        muscle="hive",
        project_path=daughter.project_path or "",
        goal=daughter.goal,
        phase="post" if daughter.cadence == CADENCE_POST else "forage",
        home=_home_of(runtime),
    )


def pulse_heartbeat(daughter: HiveDaughter, runtime: Any = None) -> None:
    from remedy.core.coordination import heartbeat

    heartbeat(
        daughter.session_id,
        phase="post" if daughter.cadence == CADENCE_POST else "forage",
        goal=daughter.goal,
        project_path=daughter.project_path or None,
        muscle="hive",
        home=_home_of(runtime),
    )


def silence_daughter(daughter: HiveDaughter, runtime: Any = None) -> None:
    from remedy.core.coordination import unregister

    unregister(daughter.session_id, home=_home_of(runtime))


def admit_packet(
    packet: ReturnPacket,
    *,
    parent_session_id: str,
    hive_id: str = "",
) -> None:
    """Write the compact packet onto the mother's evidence ledger — not a transcript."""
    sid = str(parent_session_id or "").strip()
    if not sid:
        return
    from remedy.core.metabolism.evidence import get_evidence_ledger

    body = packet.as_mother_text()
    if hive_id:
        body = f"hive_id={hive_id}\n{body}"
    get_evidence_ledger(sid).admit_tool_result(
        tool_name="hive_collect",
        content=body,
        success=bool(packet.done) and not packet.blockers,
    )


def _tool_names(fresh_calls: list[Any] | None) -> list[str]:
    from remedy.core.react_turn import extract_tool_names

    return extract_tool_names(fresh_calls if isinstance(fresh_calls, list) else None)


def inject_spawn_continue(
    messages: list[dict[str, Any]],
    fresh_calls: list[Any] | None,
) -> bool:
    """After hive_spawn / hive_assign, tell the mother to keep working.

    Returns True when a hint was appended.
    """
    names = _tool_names(fresh_calls)
    if not any(n in _HIRE_TOOLS for n in names):
        return False
    if messages:
        last = messages[-1]
        if isinstance(last, dict) and SPAWN_CONTINUE_HINT in str(last.get("content") or ""):
            return False
    messages.append({"role": "user", "content": SPAWN_CONTINUE_HINT})
    return True

"""Run a forager pulse in an isolated turn; never persist to the owner sidebar."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from remedy.core.hive.policy import (
    MAX_BUDGET_STEPS,
    hive_depth,
    reset_hive_depth,
    set_hive_depth,
)
from remedy.core.hive.store import HiveStore, get_hive_store
from remedy.core.hive.types import (
    CADENCE_POST,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REPORTED,
    STATUS_RUNNING,
    HiveDaughter,
    ReturnPacket,
    packet_from_outcome,
)

logger = logging.getLogger(__name__)

PulseFn = Callable[[Any, HiveDaughter], Awaitable[ReturnPacket]]

_pulse_impl: PulseFn | None = None
_tasks: dict[str, asyncio.Task[None]] = {}


def set_pulse_impl(fn: PulseFn | None) -> None:
    """Tests inject a fake pulse so the suite never calls a provider."""
    global _pulse_impl
    _pulse_impl = fn


def _store_for(runtime: Any) -> HiveStore:
    home = getattr(getattr(runtime, "config", None), "home_dir", None)
    return get_hive_store(home)


async def _default_llm_pulse(runtime: Any, daughter: HiveDaughter) -> ReturnPacket:
    from remedy.core.react_loop.loop import call_llm_stream
    from remedy.core.turn_context import is_turn_aborted

    old_max = getattr(runtime, "_max_react_steps", None)
    runtime._max_react_steps = max(1, min(MAX_BUDGET_STEPS, int(daughter.budget_steps or 8)))
    chunks: list[str] = []
    aborted = False
    try:
        extra = ""
        if daughter.cadence == CADENCE_POST:
            notes = (daughter.journal or {}).get("notes") or []
            recent = [
                str(n.get("outcome") or "").strip()
                for n in notes[-4:]
                if isinstance(n, dict) and str(n.get("outcome") or "").strip()
            ]
            if recent:
                extra = "\n\nJournal of prior pulses:\n" + "\n".join(
                    f"- {line}" for line in recent
                )
        charter = (
            "You are a hive daughter of Remedy. You do not speak to the owner. "
            "Report a compact outcome. Prefer tools over essays.\n\n"
            f"Job: {daughter.goal}{extra}"
        )
        async for chunk in call_llm_stream(
            runtime, charter, session_id=daughter.session_id
        ):
            if is_turn_aborted():
                aborted = True
                break
            if not str(chunk).startswith("@@"):
                chunks.append(str(chunk))
    except asyncio.CancelledError:
        aborted = True
    except Exception as exc:
        logger.warning("hive pulse failed id=%s: %s", daughter.id, exc)
        return ReturnPacket(
            goal=daughter.goal,
            done=False,
            outcome=f"pulse failed: {exc}"[:400],
            blockers=["pulse_failed"],
        )
    finally:
        if old_max is None:
            with suppress(Exception):
                delattr(runtime, "_max_react_steps")
        else:
            runtime._max_react_steps = old_max
    return packet_from_outcome(daughter.goal, "".join(chunks), aborted=aborted)


def _cancelled(packet: ReturnPacket) -> bool:
    return any("cancelled" in str(b).lower() for b in (packet.blockers or []))


async def run_isolated_pulse(runtime: Any, daughter: HiveDaughter) -> ReturnPacket:
    """One isolated ReAct pulse. Caller owns the status transition after return."""
    from remedy.core.turn_context import abort_session, begin_turn, end_turn, is_turn_aborted

    if hive_depth() >= 1:
        return ReturnPacket(
            goal=daughter.goal,
            done=False,
            outcome="refused: daughters cannot hire",
            blockers=["hive_depth"],
        )
    depth_tok = set_hive_depth(1)
    proj = daughter.project_path or ""
    if not proj:
        with suppress(Exception):
            proj = str(runtime.effective_project_path() or "")
    turn_toks = begin_turn(
        daughter.session_id,
        project_raw=proj or None,
        active_path=proj or "",
    )
    daughter.status = STATUS_RUNNING
    _store_for(runtime).save(daughter)
    packet: ReturnPacket
    try:
        impl = _pulse_impl or _default_llm_pulse
        packet = await impl(runtime, daughter)
        if is_turn_aborted():
            packet = packet_from_outcome(daughter.goal, packet.outcome, aborted=True)
    except asyncio.CancelledError:
        packet = packet_from_outcome(daughter.goal, "", aborted=True)
        raise
    finally:
        end_turn(daughter.session_id, *turn_toks)
        reset_hive_depth(depth_tok)
        with suppress(Exception):
            abort_session(daughter.session_id)
    return packet


async def run_forager(runtime: Any, daughter: HiveDaughter) -> ReturnPacket:
    """One isolated forage: own turn ContextVars, depth=1, then a packet."""
    try:
        packet = await run_isolated_pulse(runtime, daughter)
    except asyncio.CancelledError:
        packet = packet_from_outcome(daughter.goal, "", aborted=True)
        daughter.packet = packet.to_dict()
        daughter.status = STATUS_CANCELLED
        _store_for(runtime).save(daughter)
        raise
    daughter.packet = packet.to_dict()
    daughter.status = STATUS_CANCELLED if _cancelled(packet) else STATUS_REPORTED
    _store_for(runtime).save(daughter)
    return packet


async def _run_and_store(runtime: Any, daughter_id: str) -> None:
    store = _store_for(runtime)
    daughter = store.get(daughter_id)
    if daughter is None:
        return
    try:
        await run_forager(runtime, daughter)
    except asyncio.CancelledError:
        fresh = store.get(daughter_id)
        if fresh is not None and fresh.status == STATUS_RUNNING:
            fresh.status = STATUS_CANCELLED
            fresh.packet = packet_from_outcome(fresh.goal, "", aborted=True).to_dict()
            store.save(fresh)
        raise
    except Exception:
        logger.exception("hive forager crashed id=%s", daughter_id)
        fresh = store.get(daughter_id)
        if fresh is not None and fresh.status in (STATUS_PENDING, STATUS_RUNNING):
            fresh.status = STATUS_CANCELLED
            fresh.packet = ReturnPacket(
                goal=fresh.goal,
                done=False,
                outcome="forager crashed",
                blockers=["crash"],
            ).to_dict()
            store.save(fresh)
    finally:
        _tasks.pop(daughter_id, None)


def schedule_forager(runtime: Any, daughter: HiveDaughter) -> None:
    """Start the forage on the running loop; mother keeps talking."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run_and_store(runtime, daughter.id))
    _tasks[daughter.id] = task


def cancel_forager(daughter_id: str) -> bool:
    t = _tasks.get(daughter_id)
    if t is None or t.done():
        return False
    t.cancel()
    return True


def cancel_children(parent_session_id: str, runtime: Any = None) -> int:
    """Owner Stop on the mother: cancel her foragers (posts stay — PR2)."""
    store = _store_for(runtime) if runtime is not None else get_hive_store()
    n = 0
    for d in store.children_of(parent_session_id):
        if d.cadence != "forager":
            continue
        if d.status not in (STATUS_PENDING, STATUS_RUNNING):
            continue
        from remedy.core.turn_context import abort_session

        abort_session(d.session_id)
        cancel_forager(d.id)
        n += 1
    return n

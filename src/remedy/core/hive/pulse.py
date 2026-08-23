"""Standing-post pulse scheduler.

The job is continuous; the LLM is not 24/7. A post sleeps between pulses
(floor MIN_PULSE_S), then runs one isolated ReAct turn and writes a capped
packet into its journal. Owner Stop on the mother cancels foragers only —
posts keep going until hive_retire.

Resume: serve lifespan calls resume_posts so a restart does not lose the post.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from remedy.core.hive.policy import DEFAULT_POSTS, MIN_PULSE_S
from remedy.core.hive.runner import (
    _cancelled,
    _store_for,
    run_isolated_pulse,
)
from remedy.core.hive.store import HiveStore, get_hive_store
from remedy.core.hive.types import (
    CADENCE_POST,
    STATUS_ASLEEP,
    STATUS_BLOCKED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_RETIRED,
    STATUS_RUNNING,
    HiveDaughter,
    ReturnPacket,
    append_journal,
    packet_from_outcome,
)

logger = logging.getLogger(__name__)

DEFAULT_POST_PULSE_S = 120

_post_tasks: dict[str, asyncio.Task[None]] = {}
_min_pulse_override: int | None = None
_resume_started = False


def set_min_pulse_s(seconds: int | None) -> None:
    """Tests may drop the 30s floor so the scheduler can fire immediately."""
    global _min_pulse_override
    _min_pulse_override = None if seconds is None else max(0, int(seconds))


def min_pulse_s() -> int:
    if _min_pulse_override is not None:
        return _min_pulse_override
    return MIN_PULSE_S


def clamp_pulse_s(pulse_s: int | float | None) -> int:
    try:
        raw = int(pulse_s or 0)
    except (TypeError, ValueError):
        raw = 0
    floor = min_pulse_s()
    if raw <= 0:
        raw = max(floor, DEFAULT_POST_PULSE_S if floor else 0)
    return max(floor, raw)


def seconds_until(iso: str) -> float:
    stamp = str(iso or "").strip()
    if not stamp:
        return 0.0
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return max(0.0, (t - datetime.now(UTC)).total_seconds())


def mark_next_pulse(daughter: HiveDaughter, *, due_now: bool = False) -> None:
    daughter.pulse_s = clamp_pulse_s(daughter.pulse_s)
    if due_now:
        daughter.next_pulse_at = datetime.now(UTC).isoformat()
        return
    daughter.next_pulse_at = (
        datetime.now(UTC) + timedelta(seconds=daughter.pulse_s)
    ).isoformat()


async def run_post_pulse(runtime: Any, daughter: HiveDaughter) -> ReturnPacket:
    """One pulse of a standing post: packet + journal, then asleep until next."""
    store = _store_for(runtime)
    try:
        packet = await run_isolated_pulse(runtime, daughter)
    except asyncio.CancelledError:
        packet = packet_from_outcome(daughter.goal, "", aborted=True)
        fresh = store.get(daughter.id)
        if fresh is not None and fresh.status not in (STATUS_RETIRED, STATUS_CANCELLED):
            fresh.packet = packet.to_dict()
            fresh.status = STATUS_CANCELLED
            store.save(fresh)
        raise
    daughter.packet = packet.to_dict()
    append_journal(daughter, packet)
    if _cancelled(packet):
        # A cancelled pulse on a post is a skip, not retirement. Sleep it.
        daughter.status = STATUS_ASLEEP
    elif packet.blockers:
        daughter.status = STATUS_BLOCKED
    else:
        daughter.status = STATUS_ASLEEP
    mark_next_pulse(daughter)
    store.save(daughter)
    return packet


async def _post_loop(runtime: Any, daughter_id: str) -> None:
    store = _store_for(runtime)
    try:
        while True:
            daughter = store.get(daughter_id)
            if daughter is None or daughter.cadence != CADENCE_POST:
                return
            if daughter.status in (STATUS_RETIRED, STATUS_CANCELLED):
                return
            wait = seconds_until(daughter.next_pulse_at)
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            daughter = store.get(daughter_id)
            if daughter is None or daughter.status in (STATUS_RETIRED, STATUS_CANCELLED):
                return
            try:
                await run_post_pulse(runtime, daughter)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hive post pulse crashed id=%s", daughter_id)
                fresh = store.get(daughter_id)
                if fresh is not None and fresh.status not in (STATUS_RETIRED, STATUS_CANCELLED):
                    fresh.status = STATUS_BLOCKED
                    fresh.packet = ReturnPacket(
                        goal=fresh.goal,
                        done=False,
                        outcome="post pulse crashed",
                        blockers=["crash"],
                    ).to_dict()
                    mark_next_pulse(fresh)
                    store.save(fresh)
    except asyncio.CancelledError:
        return
    finally:
        _post_tasks.pop(daughter_id, None)


def schedule_post(runtime: Any, daughter: HiveDaughter) -> None:
    """Start (or replace) the standing loop for this post."""
    if daughter.cadence != CADENCE_POST:
        return
    if daughter.id in _post_tasks and not _post_tasks[daughter.id].done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if not daughter.next_pulse_at:
        mark_next_pulse(daughter, due_now=True)
        _store_for(runtime).save(daughter)
    task = loop.create_task(_post_loop(runtime, daughter.id))
    _post_tasks[daughter.id] = task


def cancel_post(daughter_id: str) -> bool:
    t = _post_tasks.get(daughter_id)
    if t is None or t.done():
        return False
    t.cancel()
    return True


def stop_all_posts() -> int:
    """Cancel in-memory loops. Disk records stay so serve can resume them."""
    n = 0
    for _did, t in list(_post_tasks.items()):
        if t.done():
            continue
        t.cancel()
        n += 1
    _post_tasks.clear()
    return n


def resume_posts(runtime: Any) -> int:
    """Wake standing posts after serve start. Idempotent for this process."""
    global _resume_started
    store = _store_for(runtime) if runtime is not None else get_hive_store()
    n = 0
    for d in store.live_posts():
        if d.status in (STATUS_RETIRED, STATUS_CANCELLED):
            continue
        # A crash mid-pulse left RUNNING — treat as due now.
        if d.status in (STATUS_PENDING, STATUS_RUNNING) or not d.next_pulse_at:
            mark_next_pulse(d, due_now=True)
            store.save(d)
        schedule_post(runtime, d)
        n += 1
    _resume_started = True
    return n


def live_post_count(store: HiveStore | None = None) -> int:
    st = store or get_hive_store()
    return len(st.live_posts())


def posts_at_capacity(store: HiveStore, cap: int = DEFAULT_POSTS) -> bool:
    return len(store.live_posts()) >= cap

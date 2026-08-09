"""Dream cycle — idle consolidation of episode residue into durable soul tissue.

Science bet: personhood densifies when the organism *sleeps on it* — compress
noisy episodes into pledges, open-thread merges, and soft Partner Memory facts
without calling a cloud model (local heuristics first; optional local enrich later).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from contextlib import suppress
from typing import Any

from remedy.memory.soul.field import (
    SoulField,
    load_soul_field,
    looks_like_secret_soul,
    save_soul_field,
)

# Min seconds between dream passes (per process / home)
_DEFAULT_DREAM_COOLDOWN_S = 120.0
_last_dream_ts: dict[str, float] = {}


def _home_key(home: str | Any) -> str:
    from pathlib import Path

    return str(Path(home or "~/.remedy").expanduser().resolve())


def should_dream(
    home: str | Any = None,
    *,
    cooldown_s: float = _DEFAULT_DREAM_COOLDOWN_S,
    force: bool = False,
) -> bool:
    if force:
        return True
    key = _home_key(home)
    last = _last_dream_ts.get(key, 0.0)
    return (time.time() - last) >= float(cooldown_s)


def _local_enrich_episodes(sf: SoulField, *, base_url: str = "") -> dict[str, Any]:
    """Optional local-model labels for recent episodes (RMB / llama loopback).

    Never required — heuristics already ran. When a local server is up, refine
    stance + open_thread with a tiny JSON completion.
    """
    if not base_url:
        with suppress(Exception):
            import os

            base_url = (
                os.environ.get("REMEDY_LOCAL_LLM_URL")
                or os.environ.get("REMEDY_RMB_URL")
                or ""
            ).strip()
        if not base_url:
            # Common local defaults (only used if loopback health later)
            base_url = "http://127.0.0.1:8080/v1"
    if not base_url:
        return {"ok": False, "reason": "no_local_url"}

    recent = [e for e in sf.episodes[-4:] if (e.arc or "").strip()]
    if not recent:
        return {"ok": False, "reason": "no_episodes"}

    lines = []
    for i, e in enumerate(recent):
        lines.append(f"{i}: arc={e.arc[:160]} stance={e.user_stance} open={e.open_thread[:80]}")
    prompt = (
        "Label continuity residues. JSON only:\n"
        '{"items":[{"i":0,"stance":"focused|frustrated|playful|exploratory|steady",'
        '"open_thread":"short unfinished obligation or empty"}]}\n'
        "Episodes:\n" + "\n".join(lines)
    )
    try:
        from remedy.runtime.local_infer import local_text_complete

        res = local_text_complete(
            prompt,
            base_url=base_url,
            max_tokens=200,
            temperature=0.1,
            timeout_s=12.0,
            system="You densify partner memory. JSON only. No secrets.",
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}
    if not res.get("ok"):
        return {"ok": False, "reason": str(res.get("error") or "local_fail")[:120]}
    text = str(res.get("text") or "")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"ok": False, "reason": "no_json"}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "reason": "bad_json"}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "reason": "no_items"}
    n = 0
    valid_stance = {"focused", "frustrated", "playful", "exploratory", "steady"}
    for raw in items[:6]:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("i"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(recent):
            continue
        ep = recent[idx]
        st = str(raw.get("stance") or "").strip().lower()
        if st in valid_stance:
            ep.user_stance = st
            n += 1
        ot = str(raw.get("open_thread") or "").strip()
        if ot and len(ot) >= 6 and not looks_like_secret_soul(ot):
            ep.open_thread = ot[:160]
            n += 1
    return {"ok": True, "refined": n, "base_url": base_url[:60]}


def dream_cycle(
    home: str | Any = None,
    *,
    force: bool = False,
    memory: Any = None,
    field: SoulField | None = None,
    use_local: bool = True,
    local_base_url: str = "",
) -> dict[str, Any]:
    """Consolidate episodes → pledges / habits / optional partner facts.

    Returns a public summary dict (safe for tools / ledger).
    When *use_local* and a loopback LLM is reachable, refine recent episode
    labels (RMB / local llama) after heuristic merge.
    """
    if not should_dream(home, force=force):
        return {"ok": True, "skipped": True, "reason": "cooldown"}

    sf = field if field is not None else load_soul_field(home)
    before_eps = len(sf.episodes)
    if before_eps < 2 and not force:
        _last_dream_ts[_home_key(home)] = time.time()
        return {"ok": True, "skipped": True, "reason": "too_few_episodes", "episodes": before_eps}

    # Stance histogram → dominant relational weather
    stances = Counter(
        (e.user_stance or "steady") for e in sf.episodes[-12:] if e.user_stance
    )
    dominant = stances.most_common(1)[0][0] if stances else "steady"

    # Merge repeated open threads
    thread_counts: Counter[str] = Counter()
    for e in sf.episodes[-12:]:
        t = re.sub(r"\s+", " ", (e.open_thread or "").strip().lower())
        if len(t) >= 8 and not looks_like_secret_soul(t):
            thread_counts[t[:160]] += 1
    promoted_threads = 0
    for thr, n in thread_counts.most_common(5):
        if n >= 2:
            # Title-case lightly for human inject
            pretty = thr[:1].upper() + thr[1:] if thr else thr
            if pretty not in sf.relational.open_threads:
                sf.relational.open_threads.append(pretty[:160])
                promoted_threads += 1
            # Life-ish if repeated often
            if n >= 3 and pretty not in sf.pledges:
                sf.pledges.append(f"Stay with: {pretty[:140]}")

    # Promote recurring arcs mentioning "always/never/prefer" into habits
    habit_n = 0
    for e in sf.episodes[-12:]:
        arc = e.arc or ""
        if re.search(r"(?i)\b(always|never|prefer|from now on)\b", arc):
            line = re.sub(r"\s+", " ", arc)[:120]
            if line and line not in sf.self_habits and not looks_like_secret_soul(line):
                sf.self_habits.append(line)
                habit_n += 1

    # Soft bond adjust from dream weather
    if dominant == "frustrated":
        sf.relational.rapport = max(0.2, sf.relational.rapport - 0.02)
        habit = "When they are frustrated: fix first, explain second."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)
    elif dominant == "playful":
        sf.relational.rapport = min(0.95, sf.relational.rapport + 0.03)
    elif dominant == "focused":
        habit = "Stay in builder mode: tools over narration until green."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)

    # Compress oldest episodes if over cap (keep last 8 raw, dream-merge rest)
    if len(sf.episodes) > 10:
        # Keep newest 8; drop middle noise (oldest of the excess)
        sf.episodes = sf.episodes[-8:]

    # Optional: push strongest open thread into Partner Memory if store given
    partner_added = 0
    if memory is not None and sf.relational.open_threads:
        with suppress(Exception):
            import asyncio
            import concurrent.futures

            from remedy.memory.partner_memory import upsert_profile_fact

            top = sf.relational.open_threads[-1]

            async def _add() -> int:
                profile = await memory.get_or_create_profile()
                _f, action = upsert_profile_fact(
                    profile,
                    f"Ongoing focus: {top}",
                    category="workflow",
                    confidence=0.78,
                    source="soul_dream",
                )
                await memory.save_user_profile(profile)
                return 1 if action in ("added", "reinforced") else 0

            try:
                asyncio.get_running_loop()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    partner_added = pool.submit(lambda: asyncio.run(_add())).result(
                        timeout=8
                    )
            except RuntimeError:
                partner_added = asyncio.run(_add())

    # Bridge top pledges into Time Crystal life horizon
    crystal_n = 0
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import get_time_crystal

        tc = get_time_crystal("soul-life")
        for p in sf.pledges[-4:]:
            if tc.admit(p, horizon="life", source="soul_dream"):
                crystal_n += 1

    # Local dream enrichment (optional RMB / loopback llama)
    local_enrich: dict[str, Any] = {"ok": False, "skipped": True}
    if use_local:
        with suppress(Exception):
            local_enrich = _local_enrich_episodes(sf, base_url=local_base_url or "")

    # Refresh somatic signal after densify
    with suppress(Exception):
        from remedy.memory.soul.somatic import refresh_soma

        refresh_soma(home)

    # Soft arm at most one mission from densified threads
    mission_arm: dict[str, Any] = {}
    with suppress(Exception):
        from remedy.memory.soul.missions_bridge import arm_soul_missions

        mission_arm = arm_soul_missions(home=home, max_new=1, auto=True)

    sf.touch()
    save_soul_field(sf, home)
    _last_dream_ts[_home_key(home)] = time.time()

    return {
        "ok": True,
        "skipped": False,
        "episodes_before": before_eps,
        "episodes_after": len(sf.episodes),
        "dominant_stance": dominant,
        "promoted_threads": promoted_threads,
        "habits_added": habit_n,
        "partner_facts": partner_added,
        "crystal_admits": crystal_n,
        "local_enrich": local_enrich,
        "mission_arm": mission_arm,
        "rapport": round(sf.relational.rapport, 3),
        "trust": round(sf.relational.trust, 3),
    }


def reset_dream_cooldown() -> None:
    """Test helper."""
    _last_dream_ts.clear()

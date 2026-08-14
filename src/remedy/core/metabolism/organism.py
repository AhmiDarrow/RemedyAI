"""Organism pulse — one living control surface for partnership + creation.

Ties Soul Field (personhood), metabolism organs (evidence, governor, crystal),
muscle (provider capability), and soma (mood) into a short system inject that
actually steers the next turn — not a status dashboard for humans.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def _life_memory_lines(home: Any, session_id: str, runtime: Any) -> list[str]:
    """Life store + eternal CAS — the organism's long-term nervous system."""
    out: list[str] = []
    with suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore

        store = LifeGoalStore(home)
        g = store.active()
        if g is not None:
            nxt = g.next_action or "name one concrete move"
            out.append(f"Life: {g.title} — next: {nxt}"[:220])
        last = store.last_step()
        if last and last.get("did"):
            out.append(f"Last I did: {last['did']}"[:180])
        # Promote durable life into the time crystal so it survives tab switches.
        if g is not None:
            with suppress(Exception):
                from remedy.core.metabolism.time_crystal import get_time_crystal

                crystal = get_time_crystal(session_id)
                crystal.admit(
                    f"Toward {g.title}: {g.next_action or 'name one move'}",
                    horizon="life",
                    source="life_store",
                )
                if last and last.get("did"):
                    crystal.admit(
                        f"Did: {last['did']}",
                        horizon="life",
                        source="life_drive",
                    )
    with suppress(Exception):
        from remedy.memory.cas import ensure_cas

        cas = ensure_cas(home)
        if cas is not None:
            snap = cas.snapshot()
            n = int(snap.get("count") or 0)
            if n:
                kinds = snap.get("kinds") or {}
                fact_n = int(kinds.get("fact") or 0) + int(kinds.get("life") or 0)
                bit = f"Memory: {n} objects"
                if fact_n:
                    bit += f" · {fact_n} durable"
                out.append(bit)
    if not out:
        with suppress(Exception):
            prof = None
            if runtime is not None:
                prof = getattr(runtime, "_user_profile", None)
            if prof is not None:
                from remedy.memory.living import life_goal_lines

                lg = life_goal_lines(prof, limit=2, home_dir=home)
                if lg:
                    out.append("Life: " + " · ".join(lg)[:200])
    return out[:3]


def organism_pulse_block(
    *,
    session_id: str = "",
    tier: int = 1,
    home: Any = None,
    runtime: Any = None,
    user_text: str = "",
    project_path: str = "",
    max_chars: int = 700,
) -> str:
    """Compact vital-signs + stance for system inject. Empty if nothing useful."""
    sid = (session_id or "").strip() or "_default"
    lines: list[str] = []
    mood = ""
    stance = "steady"
    rapport = trust = 0.0
    open_n = 0
    open_hint = ""
    muscle_label = ""
    eu = du = 0
    gov_acts: list[str] = []

    # --- Soul / soma ---
    with suppress(Exception):
        from remedy.core.feature_maturity import soul_field_enabled
        from remedy.memory.soul.field import load_soul_field
        from remedy.memory.soul.somatic import compute_soma

        if soul_field_enabled():
            sf = load_soul_field(home)
            rel = sf.relational
            rapport = float(rel.rapport or 0)
            trust = float(rel.trust or 0)
            open_n = len(rel.open_threads or [])
            if rel.open_threads:
                open_hint = str(rel.open_threads[-1])[:80]
            if sf.episodes:
                stance = str(sf.episodes[-1].user_stance or "steady")
            muscle_label = ""
            with suppress(Exception):
                from remedy.core.muscle_profile import muscle_from_runtime

                m = muscle_from_runtime(runtime)
                muscle_label = m.label
            soma = compute_soma(
                home,
                muscle_label=muscle_label,
                muscle_provider=str(getattr(runtime, "_llm_provider", "") or ""),
            )
            mood = str(soma.mood or "")
            # Soft identity name
            who = (sf.identity_name or "Remedy").strip() or "Remedy"
            lines.append(
                f"[Organism · {who} · alive] mood={soma.emoji} {soma.label} · "
                f"stance={stance} · rapport≈{rapport:.2f} trust≈{trust:.2f} · "
                f"turns={rel.turns_together}"
                + (f" · open={open_n}" if open_n else "")
            )
            if open_hint:
                lines.append(f"Open thread: {open_hint}")
            if getattr(sf, "future_dreams", None):
                lines.append("Dream: " + str(sf.future_dreams[0])[:140])
            if rel.help_mode:
                lines.append(f"Help mode they like: {rel.help_mode}")
            lines.extend(_life_memory_lines(home, sid, runtime))
            # Stance-driven partner behavior (results, not theater)
            if mood == "strained" or stance == "frustrated":
                lines.append(
                    "Stance: fix first — short, concrete, tool-backed. "
                    "No lecture; show the path or the patch."
                )
            elif mood == "playful":
                lines.append("Stance: light energy ok; still finish the work.")
            elif mood == "focused" or stance == "focused":
                lines.append(
                    "Stance: deep work — RESEARCH→PLAN→BUILD, verify before done."
                )

    # --- Metabolism counters ---
    with suppress(Exception):
        from remedy.core.metabolism.decision import get_decision_tracker
        from remedy.core.metabolism.evidence import get_evidence_ledger
        from remedy.core.metabolism.governor import get_governor

        eu = int(get_evidence_ledger(sid).evidence_units or 0)
        du = int(get_decision_tracker(sid).decision_units or 0)
        gov = get_governor(sid)
        gov_acts = list(gov.last_actions or [])
        if eu or du or int(tier) >= 2:
            bits = [f"tier=L{int(tier)}", f"EU={eu}", f"DU={du}"]
            if muscle_label:
                bits.append(f"muscle={muscle_label}")
            if gov_acts:
                bits.append("gov=" + ",".join(gov_acts[:4]))
            lines.append("Metabolism: " + " · ".join(bits))

    # --- Forge (creation muscle) when building or mid-ship ledger open ---
    forge = forge_pulse(
        user_text=user_text,
        tier=int(tier),
        runtime=runtime,
        project_path=project_path,
        session_id=sid,
        home=home,
    )
    if forge:
        lines.append(forge)
    else:
        # Still surface mid-ship resume when ledger open (even on quiet turns)
        with suppress(Exception):
            from remedy.core.build_ledger import resume_hint

            rh = resume_hint(project_path or None, home=home)
            if rh and int(tier) >= 1:
                lines.append(rh)

    # --- Immune (anti-false-done) ---
    immune = immune_pulse(
        tier=int(tier),
        session_id=sid,
        gov_actions=gov_acts,
    )
    if immune:
        lines.append(immune)

    # Nervous system — even when Soul Field is off, life + CAS still pulse.
    if not any(x.startswith("Life:") or x.startswith("Memory:") for x in lines):
        lines.extend(_life_memory_lines(home, sid, runtime))

    if not lines:
        return ""
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def forge_pulse(
    *,
    user_text: str = "",
    tier: int = 1,
    runtime: Any = None,
    project_path: str = "",
    session_id: str = "",
    home: Any = None,
) -> str:
    """Creation organ — arm builder loop when muscle + intent warrant it."""
    if int(tier) < 1:
        return ""
    ut = (user_text or "").lower()
    buildish = bool(
        any(
            k in ut
            for k in (
                "implement",
                "build",
                "ship",
                "scaffold",
                "create",
                "write",
                "fix",
                "refactor",
                "continue",
                "finish",
                "make ",
                "add ",
            )
        )
    )
    muscle_ok = False
    with suppress(Exception):
        from remedy.core.muscle_profile import muscle_from_runtime

        m = muscle_from_runtime(runtime)
        muscle_ok = m.is_capable
        if m.builder_contract and (buildish or int(tier) >= 2):
            crystal_hint = ""
            with suppress(Exception):
                from remedy.core.metabolism.time_crystal import get_time_crystal

                c = get_time_crystal(session_id, project_id=project_path or "")
                hot = c.hot_block(max_chars=220)
                if hot:
                    crystal_hint = " " + hot.replace("\n", " ")[:200]
            eu_n = 0
            with suppress(Exception):
                from remedy.core.metabolism.evidence import get_evidence_ledger

                eu_n = int(get_evidence_ledger(session_id).evidence_units or 0)
            return (
                f"[Forge · creation] muscle={m.label} parallel≤{m.max_parallel_tools}. "
                "Default: explore in batch → short plan → write → verify green → done. "
                "No monologue without tools when the work needs them."
                + (f" Evidence in hand: EU={eu_n}." if eu_n else "")
                + crystal_hint
            )
    if buildish and not muscle_ok and int(tier) >= 2:
        return (
            "[Forge · lean] Prefer tools and small verified steps over long plans."
        )
    return ""


def immune_pulse(
    *,
    tier: int = 1,
    session_id: str = "",
    gov_actions: list[str] | None = None,
) -> str:
    """Immune organ — catch false completion / secret-risk before claims stick."""
    if int(tier) < 2:
        return ""
    acts = list(gov_actions or [])
    bits: list[str] = []
    if "critical_verify" in acts or "recovery_remedy" in acts:
        bits.append(
            "Do not claim done/green/shipped without a fresh verify signal from tools."
        )
    if "shadow_strict" in acts:
        bits.append("High-blast tools: prefer file_edit paths; opaque shell stays blocked.")
    if not bits:
        with suppress(Exception):
            from remedy.core.metabolism.governor import get_governor

            g = get_governor(session_id)
            if g.verify_next:
                bits.append(
                    "Judgment point — verify tests/plan claims before asserting success."
                )
    if not bits:
        return ""
    return "[Immune] " + " ".join(bits)

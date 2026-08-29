"""Drive-to-green — the persistence that makes autonomous builds land.

A strong engineer does not stop at the first red test. She reads the
failure, fixes, re-runs, and repeats until it actually passes — or until
she's genuinely stuck and says so. The build engine had every piece
(gate tower, ranked repair hops, a per-cycle cap) but drove them exactly
once: verify → one repair → return, red or not.

This module adds the loop, as a *pure controller* so its judgment is
testable without a live model:

    iterate_to_green(verify_fn, repair_fn, ...) → outcome

- **verify_fn()** returns ``{ok: bool, progress: number}`` — progress is a
  monotone "closer to done" signal (e.g. count of passing gate levels, or
  ``-failures``). Higher is better.
- **repair_fn(verify_result)** attempts a fix and returns
  ``{ran: bool}`` — whether it actually changed anything.

The loop stops on: green, round budget, a repair that changed nothing, or
``patience`` consecutive rounds with no forward progress (anti-thrash).
Every stop has a named reason, so the agent can report honestly instead
of claiming done.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_ROUNDS = 6
DEFAULT_PATIENCE = 2


@dataclass
class DriveOutcome:
    ok: bool
    rounds: int
    reason: str  # green | budget | no_repair | no_progress | verify_error | strategies_exhausted
    progress: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = ""  # which strategy was active at the end

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rounds": self.rounds,
            "reason": self.reason,
            "progress": self.progress,
            "strategy": self.strategy,
            "history": self.history[-14:],
            "message": self._message(),
        }

    def _message(self) -> str:
        if self.ok:
            via = f" via {self.strategy}" if self.strategy else ""
            return f"Verified green after {self.rounds} repair round(s){via}."
        why = {
            "budget": "hit the repair-round budget",
            "no_repair": "a repair round changed nothing more to try",
            "no_progress": "stopped making progress (anti-thrash)",
            "strategies_exhausted": "every repair strategy stalled",
            "verify_error": "the verifier could not run",
            "unverified": "gate skipped real tests (do not claim DONE)",
        }.get(self.reason, self.reason)
        return (
            f"Not yet green after {self.rounds} round(s) — {why}. "
            "The model should read the last failure and edit directly."
        )


def _is_verified_green(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return False
    if "verified" in result:
        return bool(result.get("verified"))
    return not ("passed_levels" in result or "results" in result)


def _progress_of(result: dict[str, Any]) -> float:
    if result.get("verified") is False:
        return 0.0
    with_ = result.get("progress")
    if isinstance(with_, (int, float)):
        return float(with_)
    # Fallbacks from a gate-tower-shaped result.
    passed = result.get("passed_levels")
    if isinstance(passed, list):
        return float(len(passed))
    return 0.0


def iterate_to_green(
    verify_fn: Callable[[], dict[str, Any]],
    repair_fn: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    patience: int = DEFAULT_PATIENCE,
) -> DriveOutcome:
    """Loop verify → repair until green, budget, or stall (single strategy)."""
    return iterate_to_green_multi(
        verify_fn,
        [("repair", repair_fn)],
        max_rounds=max_rounds,
        patience=patience,
    )


def iterate_to_green_multi(
    verify_fn: Callable[[], dict[str, Any]],
    strategies: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]],
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    patience: int = DEFAULT_PATIENCE,
) -> DriveOutcome:
    """Drive to green, rotating repair STRATEGIES when one stalls.

    A single fix approach can get stuck on a failure it structurally can't
    reach (wrong file, needs a test change, needs a dependency). Instead of
    giving up when progress stalls or a repair changes nothing, advance to
    the next strategy — a different angle on the same red. Only when every
    strategy has stalled does she hand back to the model. This is what makes
    her hard to stump: persistence *and* variety.

    ``strategies`` is ordered simplest→boldest: ``[(name, repair_fn), …]``.
    """
    max_rounds = max(1, min(20, int(max_rounds)))
    patience = max(1, int(patience))
    strats = list(strategies) or [("noop", lambda _v: {"ran": False})]
    history: list[dict[str, Any]] = []

    try:
        v = verify_fn() or {}
    except Exception as exc:
        return DriveOutcome(False, 0, "verify_error", history=[{"error": str(exc)}])
    if _is_verified_green(v):
        name0 = strats[0][0]
        return DriveOutcome(True, 0, "green", _progress_of(v), [{"verify": "ok"}], name0)
    if v.get("ok") and v.get("verified") is False:
        name0 = strats[0][0]
        return DriveOutcome(
            False, 0, "unverified", _progress_of(v), [{"verify": "unverified"}], name0
        )

    best_progress = _progress_of(v)
    si = 0            # current strategy index
    stale = 0         # rounds without progress on the current strategy
    rnd = 0

    while rnd < max_rounds:
        name, repair_fn = strats[si]
        rnd += 1
        try:
            r = repair_fn(v) or {}
        except Exception as exc:
            history.append({"round": rnd, "strategy": name, "repair_error": str(exc)})
            si, stale, advanced = _advance(si, len(strats))
            if advanced:
                continue
            return DriveOutcome(False, rnd, "verify_error", best_progress, history, name)

        ran = bool(r.get("ran"))
        try:
            v = verify_fn() or {}
        except Exception as exc:
            history.append({"round": rnd, "strategy": name, "verify_error": str(exc)})
            return DriveOutcome(False, rnd, "verify_error", best_progress, history, name)

        prog = _progress_of(v)
        history.append(
            {"round": rnd, "strategy": name, "repaired": ran,
             "ok": bool(v.get("ok")), "progress": prog}
        )

        if _is_verified_green(v):
            return DriveOutcome(True, rnd, "green", prog, history, name)
        if v.get("ok") and v.get("verified") is False:
            return DriveOutcome(False, rnd, "unverified", prog, history, name)

        multi = len(strats) > 1
        # (a) The repair changed nothing — this angle is spent. Rotate to the
        # next strategy if one remains, else stop immediately.
        if not ran:
            if si + 1 < len(strats):
                si += 1
                stale = 0
                continue
            return DriveOutcome(
                False, rnd,
                "strategies_exhausted" if multi else "no_repair",
                best_progress, history, name,
            )

        if prog > best_progress:
            best_progress = prog
            stale = 0
            continue

        # (b) Repair ran but progress plateaued past `patience`.
        stale += 1
        if stale >= patience:
            if si + 1 < len(strats):
                si += 1
                stale = 0
                continue
            return DriveOutcome(
                False, rnd,
                "strategies_exhausted" if multi else "no_progress",
                best_progress, history, name,
            )

    return DriveOutcome(False, max_rounds, "budget", best_progress, history, strats[si][0])


def _advance(si: int, n: int) -> tuple[int, int, bool]:
    """Move to the next strategy after a repair crash; (idx, stale, advanced)."""
    if si + 1 < n:
        return si + 1, 0, True
    return si, 0, False


import re as _re  # noqa: E402

_VIA_RE = _re.compile(r"via\s+([a-z][a-z0-9-]*)", _re.I)


def strategy_win_counts(lessons: list[Any]) -> dict[str, int]:
    """Count green build-drive wins per strategy from organism lessons.

    Reads the ``summary`` of build-tree green lessons ("…via <strategy>.").
    Pure — accepts anything with .outcome/.tree/.summary or dict equivalents.
    """
    counts: dict[str, int] = {}
    for les in lessons or []:
        outcome = getattr(les, "outcome", None) if not isinstance(les, dict) else les.get("outcome")
        tree = getattr(les, "tree", None) if not isinstance(les, dict) else les.get("tree")
        summary = getattr(les, "summary", None) if not isinstance(les, dict) else les.get("summary")
        if str(tree or "") != "build" or str(outcome or "") != "green":
            continue
        m = _VIA_RE.search(str(summary or ""))
        if m:
            name = m.group(1).lower()
            counts[name] = counts.get(name, 0) + 1
    return counts


def order_strategy_names(
    default_names: list[str],
    lessons: list[Any],
    *,
    min_evidence: int = 3,
    margin: int = 2,
) -> list[str]:
    """Reorder repair strategies by what has actually been landing green.

    Conservative bandit-lite: keep the cheap default order UNLESS a
    later strategy has clearly out-won the first one (by ``margin`` greens,
    over at least ``min_evidence`` recorded wins). Then promote the proven
    winner to the front — start bold sooner when boldness is what works.
    """
    names = [n for n in default_names if n]
    if len(names) < 2:
        return names
    counts = strategy_win_counts(lessons)
    total = sum(counts.values())
    if total < int(min_evidence):
        return names  # not enough evidence — trust the cheap default
    first = names[0]
    best = max(names, key=lambda n: counts.get(n, 0))
    if best != first and counts.get(best, 0) >= counts.get(first, 0) + int(margin):
        return [best] + [n for n in names if n != best]
    return names


def build_lesson_from_outcome(
    outcome: DriveOutcome | dict[str, Any],
    goal: str = "",
) -> dict[str, Any] | None:
    """Translate a drive-to-green result into an organism self-lesson.

    This is how building makes her *stronger at building*: a green drive
    reinforces the strategy that landed it; a stuck drive records the class
    of failure so future turns approach it with more care. Pure — the caller
    persists via record_self_inject_lesson.
    """
    ok = bool(outcome.ok if isinstance(outcome, DriveOutcome) else outcome.get("ok"))
    rounds = int(outcome.rounds if isinstance(outcome, DriveOutcome) else outcome.get("rounds") or 0)
    reason = str(outcome.reason if isinstance(outcome, DriveOutcome) else outcome.get("reason") or "")
    strategy = str(outcome.strategy if isinstance(outcome, DriveOutcome) else outcome.get("strategy") or "")
    g = (goal or "").strip()[:80]

    # A drive that never had to repair (round 0 green) teaches nothing new.
    if ok and rounds == 0:
        return None
    if ok:
        return {
            "outcome": "green",
            "tree": "build",
            "summary": f"Drove '{g}' to green in {rounds} round(s) via {strategy or 'repair'}."[:200],
            "gate_detail": f"strategy={strategy} rounds={rounds}",
        }
    # Not green — a real stall worth remembering (skip trivial no-repair noise).
    if reason in ("no_repair",) and rounds <= 1:
        return None
    return {
        "outcome": "red",
        "tree": "build",
        "summary": f"Build '{g}' stalled: {reason} after {rounds} round(s)."[:200],
        "gate_detail": f"reason={reason} last_strategy={strategy} rounds={rounds}",
    }

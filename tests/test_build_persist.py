"""Drive-to-green — the loop that makes autonomous builds actually land."""

from __future__ import annotations

from remedy.core.build_persist import DriveOutcome, iterate_to_green


def _seq_verifier(oks: list[bool], progress: list[float] | None = None):
    """Verifier that returns each (ok, progress) in turn, holding the last."""
    calls = {"n": 0}

    def verify() -> dict:
        i = min(calls["n"], len(oks) - 1)
        p = progress[i] if progress and i < len(progress) else float(i)
        calls["n"] += 1
        return {"ok": oks[i], "progress": p}

    return verify


def _repair(ran: bool = True):
    return lambda _v: {"ran": ran}


# --- happy paths -----------------------------------------------------------


def test_already_green_zero_rounds():
    out = iterate_to_green(_seq_verifier([True]), _repair())
    assert out.ok and out.rounds == 0 and out.reason == "green"


def test_skip_pass_is_not_drive_green():
    from remedy.core.build_persist import iterate_to_green_multi

    def skip_pass() -> dict:
        return {"ok": True, "verified": False, "passed_levels": ["L3_unit"]}

    out = iterate_to_green_multi(skip_pass, [("noop", lambda _v: {"ran": False})])
    assert out.ok is False
    assert out.reason == "unverified"


def test_green_after_two_repairs():
    # red, red, green — progress climbing so anti-thrash never trips
    out = iterate_to_green(
        _seq_verifier([False, False, True], [0, 1, 2]), _repair()
    )
    assert out.ok is True
    assert out.rounds == 2 and out.reason == "green"


def test_drives_further_than_a_single_pass():
    # The old code did one repair. This needs 4 rounds to land — proves the loop.
    out = iterate_to_green(
        _seq_verifier([False, False, False, False, True], [0, 1, 2, 3, 4]),
        _repair(),
        max_rounds=6,
    )
    assert out.ok and out.rounds == 4


# --- honest stops ----------------------------------------------------------


def test_budget_exhausted_reports_not_green():
    out = iterate_to_green(
        _seq_verifier([False] * 10, list(range(10))), _repair(), max_rounds=3
    )
    assert out.ok is False and out.reason == "budget" and out.rounds == 3


def test_repair_that_changes_nothing_stops():
    out = iterate_to_green(_seq_verifier([False, False]), _repair(ran=False))
    assert out.ok is False and out.reason == "no_repair" and out.rounds == 1


def test_no_progress_trips_anti_thrash():
    # Repairs "run" but progress never improves → stop after patience rounds.
    out = iterate_to_green(
        _seq_verifier([False] * 10, [1] * 10), _repair(), patience=2
    )
    assert out.ok is False and out.reason == "no_progress"
    assert out.rounds <= 3  # first flat round + patience


def test_progress_resets_stale_counter():
    # flat, flat, then a jump, then green — the jump resets patience.
    out = iterate_to_green(
        _seq_verifier([False, False, False, True], [1, 1, 2, 3]),
        _repair(),
        patience=2,
    )
    assert out.ok is True and out.reason == "green"


# --- robustness ------------------------------------------------------------


def test_broken_verifier_is_never_a_pass():
    def boom() -> dict:
        raise RuntimeError("verifier crashed")

    out = iterate_to_green(boom, _repair())
    assert out.ok is False and out.reason == "verify_error"


def test_repair_exception_fails_safe():
    def bad_repair(_v):
        raise RuntimeError("repair blew up")

    out = iterate_to_green(_seq_verifier([False, False]), bad_repair)
    assert out.ok is False and out.reason == "verify_error"


def test_max_rounds_clamped():
    out = iterate_to_green(_seq_verifier([False] * 50), _repair(), max_rounds=999)
    assert out.rounds <= 20  # hard clamp, never runaway


def test_public_shape_and_message():
    out = iterate_to_green(_seq_verifier([False, True], [0, 1]), _repair())
    pub = out.to_public()
    assert pub["ok"] is True
    assert "green" in pub["message"].lower()
    assert isinstance(pub["history"], list)


def test_not_green_message_directs_model():
    out = DriveOutcome(False, 3, "budget")
    assert "read the last failure" in out.to_public()["message"]


# --- diverse repair: rotate strategies when one stalls --------------------

from remedy.core.build_persist import iterate_to_green_multi  # noqa: E402


def test_second_strategy_lands_when_first_stalls():
    # Strategy A never makes progress; strategy B turns it green.
    calls = {"n": 0}

    def verify():
        # green only after B has run (tracked by a mutable the strategies flip)
        calls["n"]
        calls["n"] += 1
        return {"ok": state["green"], "progress": state["prog"]}

    state = {"green": False, "prog": 0.0}

    def strat_a(_v):
        return {"ran": True}  # runs but changes nothing (prog flat)

    def strat_b(_v):
        state["green"] = True
        state["prog"] = 5.0
        return {"ran": True}

    out = iterate_to_green_multi(
        verify, [("A", strat_a), ("B", strat_b)], patience=2
    )
    assert out.ok is True
    assert out.strategy == "B"  # the bold angle landed it


def test_all_strategies_stall_reports_exhausted():
    def verify():
        return {"ok": False, "progress": 1.0}

    stall = lambda _v: {"ran": True}  # noqa: E731
    out = iterate_to_green_multi(
        verify, [("A", stall), ("B", stall), ("C", stall)], patience=1
    )
    assert out.ok is False and out.reason == "strategies_exhausted"


def test_rotation_on_repair_that_does_nothing():
    # A does nothing (ran=False) → rotate to B which fixes it.
    state = {"green": False}

    def verify():
        return {"ok": state["green"], "progress": 0.0}

    def a(_v):
        return {"ran": False}

    def b(_v):
        state["green"] = True
        return {"ran": True}

    out = iterate_to_green_multi(verify, [("A", a), ("B", b)])
    assert out.ok is True and out.strategy == "B"


def test_first_strategy_wins_if_it_works():
    state = {"green": False}

    def verify():
        return {"ok": state["green"], "progress": 0.0}

    def a(_v):
        state["green"] = True
        return {"ran": True}

    out = iterate_to_green_multi(verify, [("A", a), ("B", lambda _v: {"ran": True})])
    assert out.ok and out.strategy == "A" and out.rounds == 1


def test_crash_in_one_strategy_rotates_not_fatal():
    state = {"green": False}

    def verify():
        return {"ok": state["green"], "progress": 0.0}

    def a(_v):
        raise RuntimeError("A exploded")

    def b(_v):
        state["green"] = True
        return {"ran": True}

    out = iterate_to_green_multi(verify, [("A", a), ("B", b)])
    assert out.ok is True and out.strategy == "B"


def test_empty_strategies_safe():
    out = iterate_to_green_multi(lambda: {"ok": False}, [])
    assert out.ok is False


# --- building makes her stronger at building (organism learning) ----------

from remedy.core.build_persist import build_lesson_from_outcome  # noqa: E402


def test_green_drive_becomes_a_reinforcing_lesson():
    out = DriveOutcome(True, 3, "green", strategy="broadened")
    lesson = build_lesson_from_outcome(out, goal="add login")
    assert lesson and lesson["outcome"] == "green"
    assert "broadened" in lesson["summary"] and "3 round" in lesson["summary"]
    assert lesson["tree"] == "build"


def test_zero_round_green_teaches_nothing():
    out = DriveOutcome(True, 0, "green")
    assert build_lesson_from_outcome(out, goal="x") is None


def test_stalled_drive_becomes_a_red_lesson():
    out = DriveOutcome(False, 5, "strategies_exhausted", strategy="broadened")
    lesson = build_lesson_from_outcome(out, goal="parser refactor")
    assert lesson and lesson["outcome"] == "red"
    assert "strategies_exhausted" in lesson["gate_detail"]


def test_trivial_no_repair_is_not_recorded():
    out = DriveOutcome(False, 1, "no_repair")
    assert build_lesson_from_outcome(out, goal="x") is None


def test_lesson_accepts_public_dict_too():
    pub = DriveOutcome(True, 2, "green", strategy="source-first").to_public()
    lesson = build_lesson_from_outcome(pub, goal="y")
    assert lesson and lesson["outcome"] == "green"


def test_build_lesson_lands_in_soul_field(tmp_path):
    from remedy.memory.soul.field import clear_soul_cache, load_soul_field
    from remedy.memory.soul.update import record_self_inject_lesson

    clear_soul_cache()
    lesson = build_lesson_from_outcome(
        DriveOutcome(True, 4, "green", strategy="broadened"), goal="ship feature"
    )
    record_self_inject_lesson(home=tmp_path, **{
        "outcome": lesson["outcome"], "tree": lesson["tree"],
        "summary": lesson["summary"], "gate_detail": lesson["gate_detail"],
    })
    sf = load_soul_field(tmp_path)
    assert any(x.tree == "build" for x in sf.organism_lessons)
    clear_soul_cache()


# --- learned lessons steer future strategy order (loop fully closed) ------

from remedy.core.build_persist import order_strategy_names, strategy_win_counts  # noqa: E402


def _green(strategy: str):
    return DriveOutcome(True, 2, "green", strategy=strategy).to_public() | {"tree": "build"}


def _lesson(strategy: str):
    # Shape record_self_inject_lesson stores: build-tree green with "via X" summary.
    return {
        "outcome": "green",
        "tree": "build",
        "summary": f"Drove 'x' to green in 2 round(s) via {strategy}.",
    }


def test_win_counts_parse_strategy_from_summary():
    lessons = [_lesson("broadened"), _lesson("broadened"), _lesson("source-first")]
    counts = strategy_win_counts(lessons)
    assert counts == {"broadened": 2, "source-first": 1}


def test_non_build_or_red_lessons_ignored():
    lessons = [
        {"outcome": "green", "tree": "python", "summary": "via broadened"},
        {"outcome": "red", "tree": "build", "summary": "stalled"},
    ]
    assert strategy_win_counts(lessons) == {}


def test_default_order_kept_without_evidence():
    order = order_strategy_names(["source-first", "broadened"], [_lesson("broadened")])
    assert order == ["source-first", "broadened"]  # 1 win < min_evidence


def test_proven_winner_promoted_to_front():
    lessons = [_lesson("broadened")] * 4 + [_lesson("source-first")]
    order = order_strategy_names(["source-first", "broadened"], lessons)
    assert order[0] == "broadened"  # boldness has been winning → start bold


def test_marginal_edge_does_not_reorder():
    # broadened only 1 ahead — below margin=2 — keep the cheap default first.
    lessons = [_lesson("broadened"), _lesson("broadened"), _lesson("source-first")]
    order = order_strategy_names(["source-first", "broadened"], lessons)
    assert order[0] == "source-first"


def test_single_strategy_unchanged():
    assert order_strategy_names(["only"], []) == ["only"]


def test_full_loop_build_lesson_then_steers(tmp_path):
    # End-to-end: green drives recorded → they reorder the next selection.
    from remedy.memory.soul.field import clear_soul_cache, load_soul_field
    from remedy.memory.soul.update import record_self_inject_lesson

    clear_soul_cache()
    for _ in range(4):
        lesson = build_lesson_from_outcome(
            DriveOutcome(True, 2, "green", strategy="broadened"), goal="feat"
        )
        record_self_inject_lesson(home=tmp_path, outcome=lesson["outcome"],
                                  tree=lesson["tree"], summary=lesson["summary"],
                                  gate_detail=lesson["gate_detail"])
    lessons = list(load_soul_field(tmp_path).organism_lessons)
    order = order_strategy_names(["source-first", "broadened"], lessons)
    assert order[0] == "broadened"  # she learned to start bold
    clear_soul_cache()

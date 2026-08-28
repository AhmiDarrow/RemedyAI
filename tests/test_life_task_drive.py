"""Life-task drive — act → verify → retry → escalate, never fake done."""

from __future__ import annotations

import json

from remedy.core.computer.types import ComputerAction
from remedy.core.life_task_drive import (
    drive_life_task,
    parse_steps,
    step_is_checkpoint,
)


def test_parse_steps_accepts_json_and_list():
    assert parse_steps('[{"title": "Open", "action": "navigate"}]')[0]["action"] == (
        "navigate"
    )
    assert len(parse_steps([{"title": "a", "action": "click"}])) == 1
    assert parse_steps("") == []


def test_place_order_is_a_checkpoint():
    assert step_is_checkpoint({"title": "Place order", "action": "click", "text": "Place order"})
    assert step_is_checkpoint({"title": "Pay now", "checkpoint": True})
    assert not step_is_checkpoint({"title": "Add milk", "action": "click", "text": "Add"})


def test_checkpoint_never_runs_the_hand():
    calls: list[str] = []

    def run(action, **_kw):
        calls.append(str(action))
        return json.dumps({"ok": True, "message": "SUCCESS"})

    out = drive_life_task(
        goal="buy groceries",
        steps=[
            {"title": "Open store", "action": "navigate", "url": "https://shop.example"},
            {"title": "Place order", "action": "click", "text": "Place order"},
        ],
        run_action=run,
    )
    assert out["ok"] is False
    assert out["status"] == "need_you"
    assert calls  # navigate ran
    def _name(a: object) -> str:
        return str(getattr(a, "value", a)).lower()

    assert not any(_name(a) == "click" for a in calls)
    assert "needs you" in out["markdown"].lower() or "owner moment" in out["markdown"].lower()


def test_unverified_is_not_done():
    def run(action, **_kw):
        if action in (ComputerAction.SNAPSHOT, "snapshot"):
            return json.dumps({"ok": True, "message": "snapshot"})
        return json.dumps(
            {
                "ok": False,
                "unverified": True,
                "message": "UNVERIFIED: page probe unavailable",
            }
        )

    out = drive_life_task(
        goal="add milk",
        steps=[{"title": "Add milk", "action": "click", "text": "Add to cart"}],
        run_action=run,
        max_retries=1,
    )
    assert out["ok"] is False
    assert out["status"] == "couldnt_verify" or out["steps"][0]["block_reason"] == (
        "couldnt_verify"
    )
    assert out["steps"][0]["retries"] == 1
    assert "tool returning ok" in out["markdown"].lower() or "blocked" in out["markdown"].lower()


def test_verified_steps_mark_the_goal_done():
    def run(action, **kw):
        return json.dumps(
            {
                "ok": True,
                "message": "SUCCESS",
                "observed": {"url": kw.get("url") or "https://shop.example/cart", "title": "Cart"},
            }
        )

    out = drive_life_task(
        goal="open cart",
        steps=[
            {"title": "Open", "action": "navigate", "url": "https://shop.example"},
            {"title": "Cart", "action": "click", "text": "Cart"},
        ],
        run_action=run,
    )
    assert out["ok"] is True
    assert out["status"] == "done"
    assert all(s["status"] == "done" for s in out["steps"])
    assert "observed" in out["markdown"].lower()


def test_evidence_persists_and_resume_skips_done_steps(tmp_path):
    from remedy.core.life_task_store import load_life_task

    n = {"nav": 0}

    def run(action, **_kw):
        name = str(getattr(action, "value", action)).lower()
        if name == "navigate":
            n["nav"] += 1
            return json.dumps(
                {
                    "ok": True,
                    "message": "SUCCESS",
                    "observed": {"url": "https://shop.example", "title": "Shop"},
                }
            )
        return json.dumps({"ok": True, "message": "SUCCESS"})

    first = drive_life_task(
        goal="shop",
        steps=[
            {"title": "Open", "action": "navigate", "url": "https://shop.example"},
            {"title": "Place order", "action": "click", "text": "Place order"},
        ],
        run_action=run,
        persist=True,
        home=tmp_path,
        session_id="s1",
    )
    assert first.get("task_id")
    rec = load_life_task(first["task_id"], home=tmp_path)
    assert rec is not None
    assert rec["steps"][0]["status"] == "done"
    assert rec["steps"][1]["status"] == "need_you"

    from remedy.core.life_task_drive import resume_life_task

    again = resume_life_task(
        first["task_id"], run_action=run, home=tmp_path
    )
    assert again["status"] == "need_you"
    assert n["nav"] == 1  # did not re-navigate


def test_retry_recovers_after_one_miss():
    n = {"click": 0}

    def run(action, **_kw):
        if action in (ComputerAction.CLICK, "click"):
            n["click"] += 1
            if n["click"] == 1:
                return json.dumps({"ok": False, "message": "missed"})
            return json.dumps({"ok": True, "message": "SUCCESS"})
        return json.dumps({"ok": True, "message": "snapshot"})

    out = drive_life_task(
        goal="click add",
        steps=[{"title": "Add", "action": "click", "text": "Add"}],
        run_action=run,
        max_retries=1,
    )
    assert out["ok"] is True
    assert out["steps"][0]["retries"] == 1

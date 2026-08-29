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
    assert out["steps"][0].get("evidence_hash")
    assert len(out["steps"][0]["evidence_hash"]) == 16


def test_plan_plain_language_names_the_owner_stop():
    from remedy.core.life_task_drive import plan_plain_language

    text = plan_plain_language(
        "buy milk",
        [
            {"title": "Open store", "action": "navigate"},
            {"title": "Place order", "action": "click", "text": "Place order"},
        ],
    )
    assert "Open store" in text
    assert "stop for you" in text.lower()
    assert "Place order" in text


def test_one_plan_approval_not_per_click(monkeypatch):
    from remedy.core.approvals import ApprovalQueue

    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr("remedy.core.approvals.APPROVALS", q)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "project"},
    )
    created = {"n": 0}

    def run(action, **_kw):
        created["n"] += 1
        return json.dumps({"ok": True, "message": "SUCCESS"})

    out = drive_life_task(
        goal="add milk",
        steps=[
            {"title": "Open", "action": "navigate", "url": "https://shop.example"},
            {"title": "Add", "action": "click", "text": "Add"},
        ],
        run_action=run,
        require_plan_approval=True,
        session_id="s1",
    )
    assert out["ok"] is False
    assert "APPROVAL_REQUIRED" in out["markdown"]
    assert created["n"] == 0
    # One pending item for the whole plan, not two clicks.
    pending = [i for i in q._items.values() if i.status == "pending"]
    assert len(pending) == 1
    assert pending[0].tool_name == "life_drive"


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


def test_hub_publishes_plan_gate_and_yes_runs(monkeypatch):
    from remedy.core.approvals import ApprovalQueue
    from remedy.core.life_task_drive import act_life_task
    from remedy.core.life_task_hub import current, reset

    reset()
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr("remedy.core.approvals.APPROVALS", q)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "project"},
    )
    drive_life_task(
        goal="add milk",
        steps=[{"title": "Open", "action": "navigate", "url": "https://shop.example"}],
        run_action=lambda *a, **k: json.dumps({"ok": True, "message": "SUCCESS"}),
        require_plan_approval=True,
        session_id="s-card",
    )
    card = current("s-card")
    assert card is not None
    assert card["kind"] == "plan_gate"
    assert "Yes, No, or Explain?" in (card.get("spoken") or "")
    pending = [i for i in q._items.values() if i.status == "pending"]
    assert pending[0].summary_override
    pub = q.to_public(pending[0])
    assert pub["choices"] == ["yes", "no", "explain"]
    assert "life_drive" not in pub["summary"]

    n = {"n": 0}

    def run(action, **_kw):
        n["n"] += 1
        return json.dumps({"ok": True, "message": "SUCCESS"})

    yes = act_life_task(
        "yes",
        session_id="s-card",
        run_action=run,
    )
    assert n["n"] >= 1
    assert yes["action"] == "yes"
    assert yes.get("task")


def test_explain_does_not_run_hands(monkeypatch):
    from remedy.core.approvals import ApprovalQueue
    from remedy.core.life_task_drive import act_life_task
    from remedy.core.life_task_hub import reset

    reset()
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr("remedy.core.approvals.APPROVALS", q)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "project"},
    )
    drive_life_task(
        goal="add milk",
        steps=[{"title": "Open", "action": "navigate"}],
        run_action=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")),
        require_plan_approval=True,
        session_id="s-ex",
    )
    out = act_life_task("explain", session_id="s-ex")
    assert out["action"] == "explain"
    assert "Explain" in (out.get("spoken") or "")
    assert "add milk" in (out.get("spoken") or "").lower()


def test_checkpoint_yes_skips_and_does_not_press(tmp_path):
    from remedy.core.life_task_drive import act_life_task, resume_after_handoff
    from remedy.core.life_task_hub import reset
    from remedy.core.life_task_store import load_life_task

    reset()
    clicks: list[str] = []

    def run(action, **kw):
        name = str(getattr(action, "value", action)).lower()
        clicks.append(name)
        return json.dumps(
            {
                "ok": True,
                "message": "SUCCESS",
                "observed": {"url": kw.get("url") or "https://shop.example", "title": "Shop"},
            }
        )

    first = drive_life_task(
        goal="shop",
        steps=[
            {"title": "Open", "action": "navigate", "url": "https://shop.example"},
            {"title": "Place order", "action": "click", "text": "Place order"},
            {"title": "Done page", "action": "snapshot"},
        ],
        run_action=run,
        persist=True,
        home=tmp_path,
        session_id="s-hand",
    )
    assert first["status"] == "need_you"
    assert not any("click" in c for c in clicks)
    tid = first["task_id"]
    again = resume_after_handoff(tid, run_action=run, home=tmp_path)
    rec = load_life_task(tid, home=tmp_path)
    assert rec is not None
    assert any(s.get("status") == "skipped" for s in rec["steps"])
    assert not any("click" in c for c in clicks)
    assert again["status"] in ("done", "need_you") or again.get("ok") in (True, False)
    # Owner Yes on the card uses the same skip path.
    reset()
    first2 = drive_life_task(
        goal="shop",
        steps=[
            {"title": "Open", "action": "navigate", "url": "https://shop.example"},
            {"title": "Place order", "action": "click", "text": "Place order"},
        ],
        run_action=run,
        persist=True,
        home=tmp_path,
        session_id="s-hand2",
    )
    yes = act_life_task(
        "yes",
        session_id="s-hand2",
        task_id=first2["task_id"],
        run_action=run,
        home=tmp_path,
    )
    assert yes["action"] == "yes"
    assert not any("click" in c for c in clicks)


def test_life_task_marker_roundtrip():
    from remedy.core.life_task_hub import build_card, life_task_marker, parse_life_task_token
    from remedy.interfaces.routes.sessions.stream_tokens import parse_life_task_token as parse_sse

    card = build_card(
        goal="buy milk",
        status="running",
        spoken="Step 2 of 4 — adding milk.",
        source_steps=[{"title": "a"}, {"title": "b"}],
        steps=[{"title": "a", "status": "done"}],
    )
    tok = life_task_marker(card)
    assert tok.startswith("@@life_task:")
    parsed = parse_life_task_token(tok)
    assert parsed["goal"] == "buy milk"
    assert parsed["spoken"].startswith("Step 2")
    assert "markdown" not in parsed
    assert parse_sse(tok)["goal"] == "buy milk"

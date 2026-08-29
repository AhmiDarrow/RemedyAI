"""Handoff resume: captcha/password/2FA walls, never pay."""

from __future__ import annotations

import json

from remedy.core.life_task_drive import drive_life_task, probe_handoff
from remedy.core.life_task_handoff import (
    auto_resume_kind,
    classify_handoff,
    wall_cleared,
)
from remedy.core.life_task_hub import reset


def test_pay_never_auto_resumes():
    assert classify_handoff({"title": "Place order", "text": "Place order"}) == "pay"
    assert auto_resume_kind("pay") is False
    assert (
        wall_cleared(
            "pay",
            url="https://shop.example/done",
            paused_url="https://shop.example/checkout",
            rail_ready=True,
        )
        is False
    )


def test_captcha_clears_when_url_moves_and_rail_is_up():
    assert classify_handoff({"title": "CAPTCHA", "kind": "captcha"}) == "captcha"
    assert auto_resume_kind("captcha") is True
    assert (
        wall_cleared(
            "captcha",
            url="https://shop.example/aisle",
            paused_url="https://shop.example/challenge",
            rail_ready=True,
        )
        is True
    )
    assert (
        wall_cleared(
            "captcha",
            url="https://shop.example/aisle",
            paused_url="https://shop.example/challenge",
            rail_ready=False,
        )
        is False
    )
    assert (
        wall_cleared(
            "captcha",
            page_text="I'm not a robot",
            url="https://shop.example/aisle",
            rail_ready=True,
        )
        is False
    )


def test_probe_resumes_after_password_wall(tmp_path):
    reset()
    clicks: list[str] = []

    def run(action, **_kw):
        clicks.append(str(getattr(action, "value", action)).lower())
        return json.dumps({"ok": True, "message": "SUCCESS"})

    first = drive_life_task(
        goal="sign in",
        recipe="sign_in",
        url="https://mail.example/login",
        run_action=run,
        persist=True,
        home=tmp_path,
        session_id="s-wall",
    )
    assert first["status"] == "need_you"
    assert "click" not in clicks
    from remedy.core.life_task_hub import current

    card = current("s-wall")
    assert card is not None
    assert (card.get("handoff") or {}).get("auto") is True

    out = probe_handoff(
        session_id="s-wall",
        task_id=first["task_id"],
        url="https://mail.example/inbox",
        rail_ready=True,
        run_action=run,
        home=tmp_path,
    )
    assert out["cleared"] is True
    assert out["resumed"] is True
    assert "click" not in clicks

"""App control — Remedy driving her own interface (surface switch, panels…)."""

from __future__ import annotations

from remedy.core.app_control import (
    VALID_ACTIONS,
    app_control_bus,
    request_app_action,
)


def setup_function() -> None:
    app_control_bus().clear()


def test_switch_surface_enqueues():
    r = request_app_action("switch_surface", target="studio")
    assert r["ok"] is True
    assert r["command"]["action"] == "switch_surface"
    assert r["command"]["params"]["target"] == "studio"


def test_unknown_action_refused():
    r = request_app_action("format_c_drive")
    assert r["ok"] is False
    assert "unknown" in r["error"]


def test_take_is_fifo_and_removes():
    request_app_action("switch_surface", target="grove")
    request_app_action("focus_composer")
    bus = app_control_bus()
    first = bus.take()
    second = bus.take()
    assert first["action"] == "switch_surface"
    assert second["action"] == "focus_composer"
    assert bus.take() is None  # drained


def test_peek_does_not_remove():
    request_app_action("new_session")
    bus = app_control_bus()
    assert bus.peek()["action"] == "new_session"
    assert bus.peek()["action"] == "new_session"  # still there
    assert bus.take()["action"] == "new_session"


def test_none_params_dropped():
    r = request_app_action("open_settings")
    assert r["ok"] is True
    assert "section" not in r["command"]["params"]


def test_all_valid_actions_enqueue():
    for act in VALID_ACTIONS:
        app_control_bus().clear()
        assert request_app_action(act)["ok"] is True


def test_queue_is_capped():
    for i in range(100):
        request_app_action("focus_composer")
    # deque(maxlen=32) — never unbounded
    bus = app_control_bus()
    n = 0
    while bus.take() is not None:
        n += 1
    assert n <= 32

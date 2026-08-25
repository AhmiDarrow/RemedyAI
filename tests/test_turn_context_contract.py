"""v0.32 M1.1 — frozen TurnContext and unique turn_id."""

from __future__ import annotations

import dataclasses

import pytest

from remedy.core.context import TurnContext, TurnFactory
from remedy.core.turn_context import (
    begin_turn,
    current_turn_id,
    end_turn,
)


def test_begin_turn_assigns_unique_ids():
    t_a = begin_turn("sess-a", project_raw=None, active_path=".")
    id_a = current_turn_id()
    assert id_a
    t_b = begin_turn("sess-b", project_raw=None, active_path=".")
    id_b = current_turn_id()
    assert id_b
    assert id_a != id_b
    end_turn("sess-b", *t_b)
    assert current_turn_id() == id_a
    end_turn("sess-a", *t_a)
    assert current_turn_id() is None


def test_turn_factory_snapshot_is_frozen():
    tokens = begin_turn("sess-ctx", project_raw=None, active_path="C:\\work")
    try:
        ctx = TurnFactory.create(access_scope="full")
        assert isinstance(ctx, TurnContext)
        assert ctx.session_id == "sess-ctx"
        assert ctx.turn_id == current_turn_id()
        assert ctx.workspace.active_path == "C:\\work"
        assert ctx.workspace.access_scope == "full"
        assert ctx.cancellation.event is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.session_id = "nope"  # type: ignore[misc]
    finally:
        end_turn("sess-ctx", *tokens)


def test_cancellation_token_tracks_abort_event():
    tokens = begin_turn("sess-abort", project_raw=None, active_path=".")
    try:
        ctx = TurnFactory.create()
        assert ctx.cancellation.is_cancelled() is False
        ev = ctx.cancellation.event
        assert ev is not None
        ev.set()
        assert ctx.cancellation.is_cancelled() is True
    finally:
        end_turn("sess-abort", *tokens)

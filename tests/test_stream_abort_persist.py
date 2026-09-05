"""Abort reason / epoch guards; stream + abort HTTP are Go-owned.

Interrupted-turn durable rows and SSE abort wording live in
``native/go/httpapi/stream.go`` (+ ``stream_test.go``). FastAPI
``/messages/stream`` and ``POST /abort`` twins are gone; keep turn_context
unit coverage.
"""

from __future__ import annotations




def test_abort_reason_normalize_and_peek() -> None:
    from remedy.core.turn_context import (
        abort_session,
        normalize_abort_reason,
        peek_abort_reason,
    )

    assert normalize_abort_reason(None) == "stop"
    assert normalize_abort_reason("bogus") == "stop"
    assert normalize_abort_reason("supersede") == "supersede"
    abort_session("sess-reason-x", reason="supersede")
    assert peek_abort_reason("sess-reason-x") == "supersede"
    # Internal disconnect abort (reason=None) keeps the client's verdict.
    abort_session("sess-reason-x")
    assert peek_abort_reason("sess-reason-x") == "supersede"


def test_abort_stale_epoch_does_not_kill_a_newer_turn() -> None:
    """Family: old Stop ignored; current Stop / omit-epoch still works."""
    from remedy.core.turn_context import (
        abort_session,
        begin_turn,
        end_turn,
        is_turn_aborted,
        release_session_stream_claim,
        stream_claim_epoch,
        try_claim_session_stream,
    )

    sid = "abort-epoch-family"
    assert try_claim_session_stream(sid)
    e1 = stream_claim_epoch(sid)
    release_session_stream_claim(sid, epoch=e1)
    assert try_claim_session_stream(sid)
    e2 = stream_claim_epoch(sid)
    assert e2 != e1
    toks = begin_turn(sid, project_raw=None, active_path=".")
    try:
        n = abort_session(sid, epoch=e1, reason="stop")
        assert n == 0
        assert is_turn_aborted() is False

        n = abort_session(sid, epoch=e2, reason="stop")
        assert n == 1
        assert is_turn_aborted() is True
    finally:
        end_turn(sid, *toks)
        release_session_stream_claim(sid, epoch=e2)


def test_abort_without_epoch_still_stops_current() -> None:
    """CLI / delete omit epoch — abort whatever is current (back-compat)."""
    from remedy.core.turn_context import (
        abort_session,
        begin_turn,
        end_turn,
        is_turn_aborted,
        release_session_stream_claim,
        stream_claim_epoch,
        try_claim_session_stream,
    )

    sid = "abort-no-epoch"
    assert try_claim_session_stream(sid)
    epoch = stream_claim_epoch(sid)
    toks = begin_turn(sid, project_raw=None, active_path=".")
    try:
        n = abort_session(sid, reason="stop")
        assert n == 1
        assert is_turn_aborted() is True
    finally:
        end_turn(sid, *toks)
        release_session_stream_claim(sid, epoch=epoch)

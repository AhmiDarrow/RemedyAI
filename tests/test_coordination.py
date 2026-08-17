"""Body coordination registry — presence + path claims across sessions."""

from __future__ import annotations

import time

import pytest

from remedy.core import coordination as C


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "remedyhome"


def test_claim_succeeds_then_conflicts(home) -> None:
    # Grok claims a file.
    assert C.claim_path("grok-sess", "/proj/executor.py", muscle="xai/grok-4", home=home) is None
    # Fable tries the same file → blocked, gets Grok's beacon back.
    conflict = C.claim_path("fable-sess", "/proj/executor.py", muscle="fable", home=home)
    assert conflict is not None
    assert conflict.session_id == "grok-sess"
    assert conflict.muscle == "xai/grok-4"
    # Fable claims a different file → fine.
    assert C.claim_path("fable-sess", "/proj/store.ts", muscle="fable", home=home) is None


def test_own_reclaim_never_conflicts(home) -> None:
    assert C.claim_path("a", "/p/x.py", home=home) is None
    assert C.claim_path("a", "/p/x.py", home=home) is None  # re-claim own path


def test_release_frees_the_path(home) -> None:
    assert C.claim_path("a", "/p/x.py", home=home) is None
    assert C.claim_path("b", "/p/x.py", home=home) is not None  # blocked
    C.release_path("a", "/p/x.py", home=home)
    assert C.claim_path("b", "/p/x.py", home=home) is None  # now free


def test_unregister_releases_all(home) -> None:
    C.claim_path("a", "/p/x.py", home=home)
    C.claim_path("a", "/p/y.py", home=home)
    C.unregister("a", home=home)
    assert C.claim_path("b", "/p/x.py", home=home) is None
    assert C.claim_path("c", "/p/y.py", home=home) is None


def test_path_normalization_conflict(home) -> None:
    assert C.claim_path("a", "/p/sub/../x.py", home=home) is None
    # A different spelling of the same file still conflicts.
    conflict = C.claim_path("b", "/p/x.py", home=home)
    assert conflict is not None and conflict.session_id == "a"


def test_stale_beacon_releases_claim(home, monkeypatch) -> None:
    monkeypatch.setattr(C, "BEACON_TTL", 0.05)
    monkeypatch.setattr(C, "CLAIM_TTL", 0.05)
    assert C.claim_path("dead", "/p/x.py", home=home) is None
    time.sleep(0.08)  # beacon + claim go stale
    # A crashed/idle session must not deadlock the file.
    assert C.claim_path("alive", "/p/x.py", home=home) is None
    assert [b.session_id for b in C.active_beacons(home=home)] == ["alive"]


def test_active_beacons_and_path_holder(home) -> None:
    C.claim_path("a", "/p/x.py", muscle="xai/grok", project_path="/p", goal="build", home=home)
    C.claim_path("b", "/q/y.py", muscle="fable", project_path="/q", goal="shop", home=home)
    ids = {beacon.session_id for beacon in C.active_beacons(home=home)}
    assert ids == {"a", "b"}
    assert {beacon.session_id for beacon in C.active_beacons(exclude="a", home=home)} == {"b"}
    holder = C.path_holder("/p/x.py", exclude="b", home=home)
    assert holder is not None and holder.session_id == "a"
    assert C.path_holder("/nope.py", home=home) is None


def test_coworkers_note_names_others(home) -> None:
    C.register("a", muscle="xai/grok-4", project_path="/repo/Old-Remedy", goal="ship 0.27", phase="implement", home=home)
    C.claim_path("a", "/repo/Old-Remedy/build_engine.py", home=home)
    note = C.coworkers_note("fable-sess", home=home)
    assert "xai/grok-4" in note
    assert "Old-Remedy" in note
    assert "build_engine.py" in note
    # Working alone → empty.
    assert C.coworkers_note("a", home=home) == "" or "fable" not in C.coworkers_note("a", home=home)


def test_alone_note_is_empty(home) -> None:
    C.register("solo", muscle="xai/grok", home=home)
    assert C.coworkers_note("solo", home=home) == ""


def test_same_provider_sessions_coordinate_independently(home) -> None:
    """Multi-agent on ONE provider: 2 DeepSeek sessions + Grok + Fable.

    Coordination keys on session_id — provider is display metadata only — so
    same-provider sessions are fully independent muscles: separate beacons,
    separate claims, and conflicts detected between them like anyone else.
    """
    # Two DeepSeek sessions crunching numbers, Grok researching, Fable architecting.
    assert C.claim_path("ds-1", "/proj/stats.py", muscle="deepseek/deepseek-v4", home=home) is None
    assert C.claim_path("ds-2", "/proj/model.py", muscle="deepseek/deepseek-v4", home=home) is None
    assert C.claim_path("grok-1", "/proj/research.md", muscle="xai/grok-4.5", home=home) is None
    assert C.claim_path("fable-1", "/proj/design.md", muscle="anthropic/claude-fable-5", home=home) is None

    # All four are distinct live beacons — same provider does NOT merge them.
    assert len(C.active_beacons(home=home)) == 4

    # DeepSeek #2 trying DeepSeek #1's file conflicts (same-provider collision).
    conflict = C.claim_path("ds-2", "/proj/stats.py", muscle="deepseek/deepseek-v4", home=home)
    assert conflict is not None and conflict.session_id == "ds-1"

    # DeepSeek #1 re-claiming its OWN file is always fine.
    assert C.claim_path("ds-1", "/proj/stats.py", home=home) is None

    # One DeepSeek session ends — only ITS claims free; the twin keeps its own.
    C.unregister("ds-1", home=home)
    assert C.claim_path("ds-2", "/proj/stats.py", home=home) is None
    assert C.path_holder("/proj/model.py", exclude="grok-1", home=home).session_id == "ds-2"

    # The coworkers note distinguishes the twins by session id.
    note = C.coworkers_note("grok-1", home=home)
    assert note.count("deepseek/deepseek-v4") == 1  # ds-1 gone; ds-2 named once
    assert "ds-2"[:8] in note or "ds-2" in note

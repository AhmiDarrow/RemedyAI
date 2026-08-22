"""A final that lists its own open work is a hop boundary, not the end of the turn.

Yesterday: "proceed with all remaining fixes … only once complete report" got
nine "**Partial** … **Still open:** …" finals in a row, one hop each.
"""

from __future__ import annotations

import pytest

from remedy.core.react_open_work import (
    extract_review_findings,
    final_declares_open_work,
    message_asks_to_finish_everything,
    open_work_continue_message,
    seed_open_work_todos,
)
from tests.harness.fake_llm import FakeLLM, FakeToolRegistry, text_turn, tool_turn
from tests.test_react_loop_streaming import answer, drain, make_runtime, statuses

# --- detectors ---------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "proceed with all remaining fixes, polish, expansions and only once complete report",
        "Close all remaining gaps, keep working until none remain",
        "proceed to implement and fix until we run out of issues",
        "Take as long as it is needed and complete it all",
        "go through each lesson 1-365 one by one and expand them out",
        "close gaps",
        "do all of those things",
        "do all of that",
        "do all the fixes",
    ],
)
def test_finish_everything_requests_are_recognised(msg):
    assert message_asks_to_finish_everything(msg)


@pytest.mark.parametrize(
    "msg", ["proceed", "polish lessons", "what gaps remain", "why do you keep stopping", ""]
)
def test_ordinary_messages_are_not_finish_everything(msg):
    assert not message_asks_to_finish_everything(msg)


def test_still_open_lines_are_found_and_optional_ones_ignored():
    final = (
        "**Polish hop (partial)**\n\n"
        "- **Verify:** `npm test` → **16/16 green**\n"
        "- **Still open:** Upload UI (download MIDI / progress copy)\n\n"
        "**Still optional:** Tauri / Capacitor"
    )
    items = final_declares_open_work(final)
    assert len(items) == 1
    assert "Upload UI" in items[0]


def test_not_wired_yet_and_unchecked_boxes_count():
    final = "**Partial — engine only**\n- **Not wired yet:** `curriculum.ts`\n- [ ] GP tests"
    items = final_declares_open_work(final)
    assert any("Not wired yet" in i for i in items)
    assert any("GP tests" in i for i in items)


def test_a_complete_final_has_no_open_work():
    assert final_declares_open_work("**Done** — 55/55 green.\n- Still optional: Tauri") == []
    assert final_declares_open_work("") == []


def test_owner_choice_git_commit_is_not_open_work():
    """Session 765c 03:12: leftover 'commit if you want / no push' re-armed a done hop."""
    final = (
        "**Done — missing extensions + free Basic Pitch**\n"
        "- **Verify:** `npm test` green\n"
        "- **Still open:** local git commit if you want this hop saved · **no push**"
    )
    assert final_declares_open_work(final) == []
    assert final_declares_open_work(
        "**Partial**\n- **Still open:** Upload UI (download MIDI)"
    )


def test_continue_message_lists_the_items():
    m = open_work_continue_message(["- **Still open:** Upload UI"])
    assert m["role"] == "user"
    assert "Upload UI" in m["content"]
    assert "Do not stop here" in m["content"]
    assert "todo_write" in m["content"]


def test_seed_skips_partial_headers_and_keeps_the_real_item(tmp_path):
    from types import SimpleNamespace

    from remedy.core.build_todos import load_todos

    rt = SimpleNamespace(effective_project_path=lambda: str(tmp_path))
    n = seed_open_work_todos(
        rt,
        ["**Partial**", "- **Still open:** b.ts, tests"],
    )
    assert n == 1
    items = load_todos(rt)
    assert len(items) == 1
    assert "b.ts" in items[0].content


def test_seed_open_work_todos_puts_remaining_items_on_the_checklist(tmp_path):
    from types import SimpleNamespace

    from remedy.core.build_todos import load_todos, open_todo_count

    rt = SimpleNamespace(effective_project_path=lambda: str(tmp_path))
    n = seed_open_work_todos(rt, ["- **Still open:** playable fretting polish"])
    assert n == 1
    items = load_todos(rt)
    assert open_todo_count(items) == 1
    assert "fretting" in items[0].content.lower()


def test_extract_review_findings_from_headed_list():
    report = (
        "## ExampleProject — full codebase review\n\n"
        "Mode: read-only.\n\n"
        "## Issues\n"
        "1. MIDI warp ignores session tuning on Drop D.\n"
        "2. MusicXML tempo is dropped on round-trip.\n"
        "3. TabView badge does not show time signature.\n"
        "## Strengths\n"
        "1. Tests are green.\n"
    )
    items = extract_review_findings(report)
    assert len(items) >= 3
    assert any("MIDI warp" in i for i in items)
    assert extract_review_findings("**Issues 1-10 fixed**\n- Files: a.ts") == []
    assert extract_review_findings("**All closed.**\n\nNothing left.") == []


def test_seed_review_finding_todos(tmp_path):
    from types import SimpleNamespace

    from remedy.core.build_todos import (
        has_open_review_finding_todos,
        load_todos,
        seed_review_finding_todos,
    )

    rt = SimpleNamespace(effective_project_path=lambda: str(tmp_path))
    n = seed_review_finding_todos(
        rt, ["MIDI warp ignores Drop D", "MusicXML tempo dropped"]
    )
    assert n == 2
    items = load_todos(rt)
    assert has_open_review_finding_todos(rt)
    assert {t.id for t in items} == {"rf-1", "rf-2"}


# --- loop behaviour ----------------------------------------------------------


@pytest.mark.asyncio
async def test_still_open_final_under_finish_everything_becomes_the_next_hop(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write", results=["ok", "ok"])
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "a.ts", "content": "x"}),
            text_turn("**Partial**\n- Wired a.ts\n- **Still open:** b.ts, tests"),
            tool_turn("file_write", {"path": "b.ts", "content": "y"}),
            text_turn("**Done** — a.ts + b.ts wired, tests green."),
        ]
    )
    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "close all remaining gaps, keep working until none remain")

    assert fake.request_count == 4
    assert len(registry.calls_to("file_write")) == 2
    assert "Open work remains" in statuses(chunks)
    assert "b.ts wired" in answer(chunks)


@pytest.mark.asyncio
async def test_a_plain_request_still_accepts_a_partial_final(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write", results=["ok"])
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "a.ts", "content": "x"}),
            text_turn("**Partial**\n- Wired a.ts\n- **Still open:** b.ts"),
        ]
    )
    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "polish the upload page")

    assert fake.request_count == 2
    assert "Still open" in answer(chunks)
    assert "Open work remains" not in statuses(chunks)


@pytest.mark.asyncio
async def test_re_narrating_the_same_open_list_without_tools_stops_honestly(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write", results=["ok"])
    same = "**Partial**\n- **Still open:** b.ts"
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "a.ts", "content": "x"}),
            text_turn(same),
            text_turn(same),
            text_turn(same),
            text_turn(same),
        ],
        when_exhausted=text_turn(same),
    )
    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "keep working until none remain")

    # One continuation is granted on the first open final; the second open
    # final without any tool progress ends the turn instead of looping.
    assert statuses(chunks).count("Open work remains") == 1
    assert fake.request_count <= 4


@pytest.mark.asyncio
async def test_a_done_final_with_open_todos_is_refused(tmp_path):
    """Keep-agency after green used to accept '**Done**' while the Build
    list still had pending rows. Session 765c shipped that exact final."""
    from remedy.core.build_todos import TodoItem, save_todos

    runtime = make_runtime(tmp_path)
    save_todos(
        [TodoItem(id="1", content="melody-band octave repair", status="pending")],
        runtime,
    )
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write", results=["ok", "ok"])
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "a.ts", "content": "x"}),
            text_turn("**Done** — audio→tab quality pass."),
            tool_turn("file_write", {"path": "b.ts", "content": "y"}),
            text_turn("**Done** — melody-band wired."),
        ]
    )
    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "implement remaining converter quality")

    assert fake.request_count >= 3
    assert len(registry.calls_to("file_write")) == 2
    assert "Build list still open" in statuses(chunks) or "Open work remains" in statuses(chunks)


@pytest.mark.asyncio
async def test_still_open_final_on_an_active_build_continues_without_magic_words(tmp_path):
    """Session 765c: 'do all of those things' / a live build that reports
    **Still open** must hop again even when this message is not a
    finish-everything phrase. Green tests are not the finish line."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write", results=["ok", "ok"])
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "a.ts", "content": "x"}),
            text_turn("**Partial**\n- Wired a.ts\n- **Still open:** b.ts fretting"),
            tool_turn("file_write", {"path": "b.ts", "content": "y"}),
            text_turn("**Done** — b.ts fretting wired."),
        ]
    )
    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "implement remaining converter quality")

    assert "Open work remains" in statuses(chunks)
    assert len(registry.calls_to("file_write")) == 2
    assert "fretting wired" in answer(chunks) or "b.ts" in answer(chunks)

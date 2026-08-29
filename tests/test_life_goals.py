"""Life goals: durable store + life-turn tool pack."""

from __future__ import annotations

from pathlib import Path

from remedy.core.react_turn import resolve_tools
from remedy.memory.life_goals import (
    LifeGoalStore,
    format_goals_markdown,
    looks_like_life_goal_statement,
)
from remedy.memory.living import life_goal_lines, turn_kind


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def test_store_add_list_complete_and_next(tmp_path):
    store = LifeGoalStore(tmp_path)
    g = store.add("Finish the novel this year", why="it's been ten years", horizon="life")
    assert g.status in ("open", "active")
    assert store.open_count() == 1
    again = store.add("Finish the novel this year")
    assert again.id == g.id
    store.set_next("novel", "Outline chapter 3", next_by="Thursday")
    active = store.active()
    assert active is not None
    assert "chapter 3" in active.next_action
    done = store.complete("novel", evidence="outlined in Scratch")
    assert done is not None
    assert done.status == "done"
    assert store.open_count() == 0
    md = format_goals_markdown(store.list(include_closed=True))
    assert "Finish the novel" in md


def test_corrupt_life_goals_json_backs_up_and_resumes(tmp_path):
    # A corrupt file must be preserved (not silently destroyed) AND must not
    # permanently disable future writes — it is moved aside to .corrupt and a
    # fresh store accepts new goals.
    store = LifeGoalStore(tmp_path)
    store.add("Keep this goal")
    store.path.write_text("{not json", encoding="utf-8")
    broken = LifeGoalStore(tmp_path)
    assert broken.list() == []          # corrupt read yields empty, not a crash
    broken.add("New after corrupt")     # writes resume (old bug: silent no-op)
    assert any(g.title == "New after corrupt" for g in LifeGoalStore(tmp_path).list())
    backup = store.path.with_suffix(".corrupt")
    assert backup.is_file() and "{not json" in backup.read_text(encoding="utf-8")


def test_life_goal_lines_prefer_store(tmp_path):
    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    lines = life_goal_lines(None, home_dir=tmp_path)
    assert any("Land the job" in x and "resume" in x for x in lines)


def test_turn_kind_and_statement():
    assert turn_kind("I want to finish my novel this year") in ("life", "goal", "general")
    assert looks_like_life_goal_statement("I want to finish my novel this year") is True
    assert looks_like_life_goal_statement("implement the file_write path") is False
    assert looks_like_life_goal_statement("1 + 1") is False
    assert looks_like_life_goal_statement(
        "launch the site locally so I can see the new remedy page"
    ) is False


def test_resolve_tools_does_not_strip_file_tools_on_life_shaped_words():
    """Phrase lists must not choose the tool pack.

    ``shipping`` in "not for shipping" used to arm only goal/help/web and
    leave the model looping on companion_context.
    """
    all_t = [
        _tool("file_write"),
        _tool("file_edit"),
        _tool("file_read"),
        _tool("host_run"),
        _tool("goal_add"),
        _tool("goal_list"),
        _tool("memory_save"),
        _tool("web_search"),
        _tool("help_list"),
        _tool("companion_context"),
    ]
    novel = resolve_tools(
        message="I want to finish my novel this year",
        all_tools=all_t,
        turn_tier=1,
    )
    novel_names = {
        str((t.get("function") or {}).get("name"))
        for t in (novel.tools or [])
    }
    assert novel.reason != "life_goal"
    assert "file_write" in novel_names
    assert "host_run" in novel_names

    bot = resolve_tools(
        message=(
            "modify the remedy-social-bot so it doesn't need API credentials — "
            "use already signed-in browser sessions on Reddit and X. "
            "Local-only, not for shipping."
        ),
        all_tools=all_t,
        turn_tier=1,
    )
    bot_names = {
        str((t.get("function") or {}).get("name"))
        for t in (bot.tools or [])
    }
    assert bot.reason != "life_goal"
    assert "file_write" in bot_names
    assert "file_read" in bot_names
    assert "host_run" in bot_names


def test_drive_and_pulse(tmp_path):
    from remedy.memory.life_goals import drive_markdown, pulse_due, weekly_pulse

    store = LifeGoalStore(tmp_path)
    store.add("Write the book", next_action="Outline chapter 3")
    drive = drive_markdown(tmp_path)
    assert "Write the book" in drive
    assert "chapter 3" in drive
    pulse = weekly_pulse(tmp_path)
    assert pulse["open"] == 1
    assert "This week" in pulse["markdown"]
    assert pulse_due(tmp_path, days=7) is True
    store.record_pulse()
    assert pulse_due(tmp_path, days=7) is False


def test_add_and_step_is_local_without_force(tmp_path, monkeypatch):
    """API create must not startfile / web-search (force=False)."""
    from remedy.memory.life_drive import add_and_step

    revealed: list[str] = []
    monkeypatch.setattr(
        "remedy.memory.life_drive.reveal_artifact",
        lambda path: revealed.append(str(path)) or False,
    )
    monkeypatch.setenv("REMEDY_NO_REVEAL", "1")
    g = add_and_step(tmp_path, "Gauntlet hold 1", source="api", force=False)
    assert g.title == "Gauntlet hold 1"
    assert g.next_action
    assert revealed == []


def test_take_step_writes_note_and_advances(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_LIFE_NOTES", "1")  # note FILES are opt-in now
    from remedy.memory.life_drive import classify_action, invent_next, take_step
    from remedy.memory.life_goals import LifeGoal

    assert classify_action("send the application") == "irreversible"
    assert classify_action("draft an outline") == "draft"
    fake = LifeGoal(id="x", title="Finish the novel")
    assert "outline" in invent_next(fake).lower()
    store = LifeGoalStore(tmp_path)
    store.add("Finish the novel")
    out = take_step(tmp_path)
    assert out["ok"] is True
    assert Path(out["path"]).is_file()
    assert "Did:" in out["markdown"]
    g = store.active()
    assert g is not None
    assert g.next_action
    assert g.evidence
    # irreversible is not auto-done
    store.set_next("novel", "send the query letter")
    skip = take_step(tmp_path)
    assert skip["ok"] is False
    assert skip["skipped"] == "needs_you"


def test_classify_life_drive_is_l0():
    from remedy.core.metabolism.tier import TurnTier, classify_turn_tier

    assert classify_turn_tier("what should I do on my goals?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what should I do?") != TurnTier.L0_INSTANT
    assert classify_turn_tier("how am I doing?") == TurnTier.L0_INSTANT


def test_l0_what_should_i_do(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.metabolism.l0 import try_l0_system_reply
    from remedy.memory.life_goals import LifeGoalStore

    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(tmp_path)))
    text = try_l0_system_reply(rt, "what should I do on my goals?", preclassified=True)
    assert text and "Land the job" in text
    pulse = try_l0_system_reply(rt, "how am I doing?", preclassified=True)
    assert pulse and "This week" in pulse


def test_visible_notes_when_documents_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_LIFE_NOTES", "1")  # note FILES are opt-in now
    from remedy.memory.life_drive import resolve_life_notes_dir, take_step

    docs = tmp_path / "Documents"
    docs.mkdir()
    folder = resolve_life_notes_dir(tmp_path, documents_root=docs)
    assert folder == docs / "Remedy Life"
    assert (folder / "README.md").is_file()
    store = LifeGoalStore(tmp_path)
    store.add("Finish the novel")
    out = take_step(tmp_path, documents_root=docs)
    assert out["ok"] is True
    note = Path(out["path"])
    assert note.is_file()
    assert note.parent == folder
    # Canonical copy under the Remedy home
    canon = tmp_path / "life" / note.name
    assert canon.is_file()


def test_notice_step_and_goal_done(tmp_path):
    from remedy.memory.life_drive import notice_progress, take_step

    store = LifeGoalStore(tmp_path)
    store.add("Land the job", next_action="Rewrite the resume")
    take_step(tmp_path)
    noticed = notice_progress(tmp_path, "I did it")
    assert noticed["ok"] is True
    assert noticed["kind"] == "step_done"
    g = store.active()
    assert g is not None
    assert g.status in ("open", "active")
    assert "you did:" in " ".join(g.evidence).lower() or g.next_action
    done = notice_progress(tmp_path, "the goal is done")
    assert done["ok"] is True
    assert done["kind"] == "goal_done"
    assert store.open_count() == 0


def test_digest_after_step(tmp_path):
    from remedy.memory.life_drive import drive_digest, take_step

    store = LifeGoalStore(tmp_path)
    store.add("Write the book", next_action="Outline chapter 3")
    take_step(tmp_path)
    first = drive_digest(tmp_path, mark_seen=True)
    assert first["unseen"] >= 1
    assert "Outline chapter 3" in first["markdown"] or "While you were away" in first["markdown"]
    again = drive_digest(tmp_path, mark_seen=True)
    assert again["unseen"] == 0
    assert "No new Life steps" in again["markdown"] or again["steps"] == []


def test_research_embeds_web_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_LIFE_NOTES", "1")  # note FILES are opt-in now
    from remedy.memory import life_drive

    store = LifeGoalStore(tmp_path)
    store.add("Learn Spanish", next_action="research beginner resources")
    monkeypatch.setattr(
        life_drive,
        "_search_web",
        lambda q, max_results=3: [
            {
                "title": "BBC Languages",
                "url": "https://example.com/spanish",
                "snippet": "Free beginner lessons",
            }
        ],
    )
    out = life_drive.take_step(tmp_path, allow_web=True)
    assert out["ok"] is True
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "BBC Languages" in text
    assert "did not apply" in text.lower() or "sign up" in text.lower()


def test_l0_notice_and_digest(tmp_path):
    from types import SimpleNamespace

    from remedy.core.metabolism.l0 import try_l0_system_reply
    from remedy.core.metabolism.tier import TurnTier, classify_turn_tier
    from remedy.memory.life_drive import take_step

    assert classify_turn_tier("I did it") == TurnTier.L0_INSTANT
    assert classify_turn_tier("I'm back") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what did you do?") == TurnTier.L0_INSTANT

    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    take_step(tmp_path)
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(tmp_path)))
    noticed = try_l0_system_reply(rt, "I did it", preclassified=True)
    assert noticed and "you did" in noticed.lower()
    digest = try_l0_system_reply(rt, "I'm back", preclassified=True)
    assert digest and ("away" in digest.lower() or "next" in digest.lower() or "did" in digest.lower())


def test_resolve_tools_coding_still_armed():
    all_t = [_tool("file_write"), _tool("goal_add")]
    d = resolve_tools(
        message="implement the login form in src/app.tsx",
        all_tools=all_t,
        turn_tier=1,
    )
    names = {
        str((t.get("function") or {}).get("name"))
        for t in (d.tools or [])
    }
    assert "file_write" in names
    assert d.reason != "life_goal"


def test_resolve_tools_launch_site_keeps_host_tools():
    """'launch the site' used to classify as a life goal and strip file/host tools."""
    all_t = [
        _tool("file_read"),
        _tool("host_run"),
        _tool("bash_exec"),
        _tool("computer_navigate"),
        _tool("goal_add"),
        _tool("help_list"),
    ]
    d = resolve_tools(
        message="launch the site locally so I can see the new remedy page",
        all_tools=all_t,
        turn_tier=1,
    )
    names = {
        str((t.get("function") or {}).get("name"))
        for t in (d.tools or [])
    }
    assert d.reason != "life_goal"
    assert "file_read" in names
    assert "host_run" in names or "bash_exec" in names
    assert "computer_navigate" in names

    d2 = resolve_tools(
        message="I want to launch the site locally",
        all_tools=all_t,
        turn_tier=1,
    )
    names2 = {
        str((t.get("function") or {}).get("name"))
        for t in (d2.tools or [])
    }
    assert d2.reason != "life_goal"
    assert "host_run" in names2 or "file_read" in names2

    tired = resolve_tools(
        message="I'm tired of this failing",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
    )
    tired_names = {
        str((t.get("function") or {}).get("name"))
        for t in (tired.tools or [])
    }
    assert tired.reason != "life_goal"
    assert "file_read" in tired_names


def test_life_note_files_off_by_default(tmp_path, monkeypatch):
    """Owner request (Aug 2026): stop writing life notes — files are opt-in."""
    from remedy.memory.life_drive import take_step

    monkeypatch.delenv("REMEDY_LIFE_NOTES", raising=False)
    store = LifeGoalStore(tmp_path)
    store.add("Finish the novel")
    out = take_step(tmp_path)
    assert out["ok"] is True
    assert out["path"] == ""  # no note file written
    assert "off" in out["markdown"].lower() or "logged" in out["markdown"].lower()
    life_dir = tmp_path / "life"
    assert not life_dir.is_dir() or not list(life_dir.glob("*.md"))
    # Goal still advances — tracking continues, only the files stopped.
    g = store.active()
    assert g is not None and g.next_action


def test_delete_goal_removes_it(tmp_path):
    store = LifeGoalStore(tmp_path)
    g = store.add("Temporary goal")
    assert store.delete(g.id) is True
    assert not any(x.id == g.id for x in store.list(include_closed=True))
    assert store.delete("nonexistent-id") is False


def test_patch_renames_goal(tmp_path):
    store = LifeGoalStore(tmp_path)
    g = store.add("Old name")
    store.patch(g.id, title="New name")
    assert any(x.title == "New name" for x in LifeGoalStore(tmp_path).list())


def test_operational_instructions_not_recorded_as_activity(tmp_path):
    store = LifeGoalStore(tmp_path)
    store.record_drive({"did": "HOST: start serve.py port 5173", "goal": "run python serve.py"})
    store.record_drive({"did": "npm run build then open browser", "goal": "x"})
    assert store.last_step() is None  # operational junk filtered out


def test_real_activity_still_recorded(tmp_path):
    store = LifeGoalStore(tmp_path)
    store.record_drive({"did": "Drafted resume bullets", "goal": "Land the job"})
    ls = store.last_step()
    assert ls and "resume" in ls["did"]


def test_clear_activity_wipes_last_step(tmp_path):
    store = LifeGoalStore(tmp_path)
    store.record_drive({"did": "Drafted an outline", "goal": "Finish the novel"})
    assert store.last_step() is not None
    store.clear_activity()
    assert LifeGoalStore(tmp_path).last_step() is None


def test_delete_is_exact_not_substring(tmp_path):
    store = LifeGoalStore(tmp_path)
    store.add("Ship app")
    store.add("Ship app v2")
    # Deleting by the exact title of A must NOT also remove B.
    assert store.delete("Ship app") is True
    remaining = {g.title for g in LifeGoalStore(tmp_path).list()}
    assert "Ship app v2" in remaining and "Ship app" not in remaining


def test_delete_by_id_removes_one(tmp_path):
    store = LifeGoalStore(tmp_path)
    a = store.add("Alpha")
    store.add("Alphabet soup")
    assert store.delete(a.id) is True
    titles = {g.title for g in LifeGoalStore(tmp_path).list()}
    assert titles == {"Alphabet soup"}

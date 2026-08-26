"""Retention — what gets deleted, and much more importantly what does not.

Everything here removes the owner's own data permanently. There is no undo, so
the properties that matter are the negative ones: ``0`` means never purge that
category, a file inside the window is never touched, and a directory that does
not exist is not an error. A retention pass that over-deletes by a day is worse
than one that never runs.

Real files with aged mtimes, in a throwaway home.
"""

from __future__ import annotations

import os
import time

import pytest

from remedy.core.retention import (
    RetentionPolicy,
    purge_attachments,
    purge_computer_shots,
    purge_logs,
    purge_old_sessions,
    purge_undo,
    run_retention_pass,
)

DAY = 86400


def aged(path, *, days: float):
    """Write a file and backdate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    when = time.time() - days * DAY
    os.utime(path, (when, when))
    return path


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "remedy-home"


# --- the policy --------------------------------------------------------------


def test_the_defaults_keep_a_long_history():
    p = RetentionPolicy.from_config(None)
    assert p.session_days == 180
    assert p.attachment_days == 90
    assert p.computer_shot_days == 14
    assert p.undo_days == 30
    assert p.log_days == 30
    assert p.event_days == 14


def test_a_nested_config_block_is_read():
    p = RetentionPolicy.from_config({"retention": {"session_days": 7}})
    assert p.session_days == 7


def test_a_flat_key_is_read_too():
    """Both spellings appear in configs written by different versions."""
    assert RetentionPolicy.from_config({"retention_session_days": 7}).session_days == 7


def test_the_nested_block_wins_over_the_flat_key():
    p = RetentionPolicy.from_config(
        {"retention": {"session_days": 7}, "retention_session_days": 999}
    )
    assert p.session_days == 7


def test_zero_is_a_real_setting_and_not_a_missing_one():
    """0 means never purge. Treating it as unset would start deleting."""
    assert RetentionPolicy.from_config({"retention": {"session_days": 0}}).session_days == 0


def test_a_negative_value_is_clamped_to_never():
    assert RetentionPolicy.from_config({"retention": {"undo_days": -5}}).undo_days == 0


def test_an_absurd_value_is_clamped():
    assert RetentionPolicy.from_config({"retention": {"log_days": 10**9}}).log_days == 3650


def test_a_nonsense_value_falls_back_to_the_default():
    assert RetentionPolicy.from_config({"retention": {"log_days": "soon"}}).log_days == 30


def test_a_null_value_is_treated_as_unset():
    assert RetentionPolicy.from_config({"retention": {"log_days": None}}).log_days == 30


@pytest.mark.parametrize("cfg", [None, {}, "not a dict", 7, []])
def test_a_missing_or_malformed_config_still_yields_defaults(cfg):
    assert RetentionPolicy.from_config(cfg).session_days == 180


# --- purging files -----------------------------------------------------------


def test_an_old_attachment_is_removed(home):
    old = aged(home / "attachments" / "s1" / "old.png", days=200)
    assert purge_attachments(home, max_age_days=90) == 1
    assert not old.exists()


def test_a_recent_attachment_is_kept(home):
    fresh = aged(home / "attachments" / "s1" / "fresh.png", days=2)
    assert purge_attachments(home, max_age_days=90) == 0
    assert fresh.exists()


def test_a_file_just_inside_the_window_is_kept(home):
    """Off-by-one here is a deleted file the owner still wanted."""
    edge = aged(home / "attachments" / "s1" / "edge.png", days=89)
    purge_attachments(home, max_age_days=90)
    assert edge.exists()


def test_attachments_are_purged_through_their_session_subdirectories(home):
    aged(home / "attachments" / "s1" / "a.png", days=200)
    aged(home / "attachments" / "s2" / "deep" / "b.png", days=200)
    assert purge_attachments(home, max_age_days=90) == 2


def test_an_emptied_session_directory_is_tidied_away(home):
    aged(home / "attachments" / "s1" / "a.png", days=200)
    purge_attachments(home, max_age_days=90)
    assert not (home / "attachments" / "s1").exists()


def test_a_session_directory_with_anything_left_is_kept(home):
    aged(home / "attachments" / "s1" / "old.png", days=200)
    aged(home / "attachments" / "s1" / "new.png", days=1)
    purge_attachments(home, max_age_days=90)
    assert (home / "attachments" / "s1" / "new.png").exists()


@pytest.mark.parametrize(
    ("purge", "path"),
    [
        (purge_attachments, "attachments/s1/x.png"),
        (purge_computer_shots, "computer/shots/x.png"),
        (purge_undo, "undo/x.jsonl"),
        (purge_logs, "logs/x.log"),
    ],
)
def test_zero_days_never_deletes_anything(home, purge, path):
    """The setting an owner uses to say "keep all of it"."""
    kept = aged(home / path, days=10_000)
    assert purge(home, max_age_days=0) == 0
    assert kept.exists()


@pytest.mark.parametrize(
    "purge", [purge_attachments, purge_computer_shots, purge_undo, purge_logs]
)
def test_a_home_that_does_not_exist_is_not_an_error(tmp_path, purge):
    assert purge(tmp_path / "never-created", max_age_days=1) == 0


def test_screenshots_are_purged_from_every_place_they_land(home):
    for sub in ("shots", "screenshots", "captures"):
        aged(home / "computer" / sub / "shot.png", days=30)
    aged(home / "computer" / "loose.png", days=30)
    assert purge_computer_shots(home, max_age_days=14) == 4


def test_a_non_image_under_computer_is_left_alone(home):
    """The job queue lives here too; retention must not eat it."""
    job = aged(home / "computer" / "jobs.json", days=300)
    purge_computer_shots(home, max_age_days=14)
    assert job.exists()


@pytest.mark.parametrize("name", ["a.jsonl", "b.json"])
def test_undo_trails_are_purged_by_extension(home, name):
    aged(home / "undo" / name, days=90)
    assert purge_undo(home, max_age_days=30) == 1


def test_something_that_is_not_an_undo_trail_is_left_alone(home):
    other = aged(home / "undo" / "README.md", days=300)
    purge_undo(home, max_age_days=30)
    assert other.exists()


@pytest.mark.parametrize("name", ["remedy.log", "remedy.log.1", "old.gz"])
def test_rotated_logs_are_purged(home, name):
    aged(home / "logs" / name, days=90)
    assert purge_logs(home, max_age_days=30) == 1


def test_a_directory_is_never_counted_as_a_purged_file(home):
    (home / "undo" / "a-directory.json").mkdir(parents=True)
    assert purge_undo(home, max_age_days=1) == 0


# --- purging sessions --------------------------------------------------------


class StoreWithBulkDelete:
    def __init__(self, n=3) -> None:
        self.called_with = None
        self._n = n

    def purge_sessions_older_than_days(self, days):
        self.called_with = days
        return self._n


def test_a_store_that_can_bulk_delete_is_asked_to(home):
    store = StoreWithBulkDelete()
    assert purge_old_sessions(store, max_age_days=180) == 3
    assert store.called_with == 180


def test_a_failing_bulk_delete_is_reported_as_zero_not_a_crash(caplog):
    class Broken:
        def purge_sessions_older_than_days(self, days):
            raise RuntimeError("database is locked")

    with caplog.at_level("WARNING"):
        assert purge_old_sessions(Broken(), max_age_days=180) == 0
    assert any("retention" in r.message for r in caplog.records)


def test_no_store_purges_nothing():
    assert purge_old_sessions(None, max_age_days=180) == 0


def test_zero_days_never_purges_sessions():
    store = StoreWithBulkDelete()
    assert purge_old_sessions(store, max_age_days=0) == 0
    assert store.called_with is None


def test_a_store_with_only_list_and_delete_is_walked():
    old = time.time() - 400 * DAY

    class Basic:
        def __init__(self):
            self.deleted = []

        def list_sessions(self):
            return [
                {"id": "old", "updated_at": old},
                {"id": "recent", "updated_at": time.time()},
            ]

        def delete_session(self, sid):
            self.deleted.append(sid)

    store = Basic()
    assert purge_old_sessions(store, max_age_days=180) == 1
    assert store.deleted == ["old"]


def test_an_iso_timestamp_is_understood():
    from datetime import UTC, datetime, timedelta

    stamp = (datetime.now(UTC) - timedelta(days=400)).isoformat().replace("+00:00", "Z")

    class Basic:
        def __init__(self):
            self.deleted = []

        def list_sessions(self):
            return [{"id": "old", "created_at": stamp}]

        def delete_session(self, sid):
            self.deleted.append(sid)

    store = Basic()
    assert purge_old_sessions(store, max_age_days=180) == 1


def test_a_session_with_no_usable_timestamp_is_kept():
    """Unknown age is not old age — never delete on a guess."""

    class Basic:
        def __init__(self):
            self.deleted = []

        def list_sessions(self):
            return [{"id": "mystery", "updated_at": "not a date"}, {"id": "none"}]

        def delete_session(self, sid):
            self.deleted.append(sid)

    store = Basic()
    assert purge_old_sessions(store, max_age_days=180) == 0
    assert store.deleted == []


def test_a_store_with_no_usable_api_purges_nothing():
    assert purge_old_sessions(object(), max_age_days=180) == 0


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_an_async_listing_is_left_to_the_caller():
    """Silently treating a coroutine as an empty list would look like success.

    The un-awaited coroutine is the point: retention declines to drive an async
    store from sync code rather than guessing, so nothing awaits it here.
    """

    class AsyncStore:
        async def list_sessions(self):
            return []

        def delete_session(self, sid):
            raise AssertionError("must not delete")

    assert purge_old_sessions(AsyncStore(), max_age_days=180) == 0


# --- the whole pass ----------------------------------------------------------


def test_a_pass_reports_what_it_removed_in_each_category(home):
    aged(home / "attachments" / "s1" / "a.png", days=200)
    aged(home / "computer" / "shots" / "b.png", days=200)
    aged(home / "undo" / "c.jsonl", days=200)
    aged(home / "logs" / "d.log", days=200)
    out = run_retention_pass({"home_dir": str(home)}, store=StoreWithBulkDelete(n=2))
    assert out == {
        "attachments": 1,
        "computer_shots": 1,
        "undo": 1,
        "logs": 1,
        "events": 0,
        "sessions": 2,
    }


def test_a_pass_on_a_fresh_install_removes_nothing(home):
    home.mkdir(parents=True)
    assert sum(run_retention_pass({"home_dir": str(home)}).values()) == 0


def test_an_explicit_home_overrides_the_config(tmp_path):
    real = tmp_path / "real"
    aged(real / "logs" / "d.log", days=200)
    out = run_retention_pass({"home_dir": str(tmp_path / "elsewhere")}, home=real)
    assert out["logs"] == 1


def test_sessions_are_left_alone_when_no_store_is_given(home):
    home.mkdir(parents=True)
    assert run_retention_pass({"home_dir": str(home)})["sessions"] == 0


def test_a_policy_of_all_zeros_deletes_nothing(home):
    aged(home / "attachments" / "s1" / "a.png", days=10_000)
    aged(home / "logs" / "d.log", days=10_000)
    cfg = {
        "home_dir": str(home),
        "retention": {
            "session_days": 0,
            "attachment_days": 0,
            "computer_shot_days": 0,
            "undo_days": 0,
            "log_days": 0,
        },
    }
    assert sum(run_retention_pass(cfg, store=StoreWithBulkDelete()).values()) == 0
    assert (home / "attachments" / "s1" / "a.png").exists()

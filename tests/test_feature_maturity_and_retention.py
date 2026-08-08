"""Maturity gates, retention policy, safer bootstrap defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from remedy.core.feature_maturity import (
    build_os_advanced_enabled,
    feature_enabled,
    maturity_snapshot,
    rmb_enabled,
    soul_field_enabled,
)
from remedy.core.retention import (
    RetentionPolicy,
    memory_encryption_requested,
    purge_attachments,
    run_retention_pass,
)
from remedy.interfaces.local_auth import http_bootstrap_enabled


def test_maturity_defaults() -> None:
    cfg: dict = {}
    # Soul Field is on by default so the organism lives; opt-out still works.
    assert soul_field_enabled(cfg) is True
    assert build_os_advanced_enabled(cfg) is False
    assert rmb_enabled(cfg) is True
    snap = maturity_snapshot(cfg)
    assert snap["soul_field_maturity"] == "stable"
    assert snap["build_os_advanced_maturity"] == "advanced"
    assert soul_field_enabled({"soul_field_enabled": False}) is False


def test_maturity_opt_in() -> None:
    cfg = {"soul_field_enabled": True, "build_os_advanced": "yes", "rmb_enabled": 0}
    assert soul_field_enabled(cfg) is True
    assert build_os_advanced_enabled(cfg) is True
    assert rmb_enabled(cfg) is False
    assert feature_enabled("soul_field_enabled", cfg=cfg) is True


def test_retention_policy_from_config() -> None:
    p = RetentionPolicy.from_config(
        {
            "retention_session_days": 90,
            "retention_attachment_days": 7,
            "memory_encrypt": True,
        }
    )
    assert p.session_days == 90
    assert p.attachment_days == 7
    assert p.computer_shot_days == 14  # default soft
    assert memory_encryption_requested({"memory_encrypt": True}) is True
    assert memory_encryption_requested({}) is False

    # Flat retention_* keys must win even when short-key defaults are non-zero
    p2 = RetentionPolicy.from_config(
        {
            "retention_computer_shot_days": 3,
            "retention_log_days": 5,
            "retention_undo_days": 9,
        }
    )
    assert p2.computer_shot_days == 3
    assert p2.log_days == 5
    assert p2.undo_days == 9

    # Explicit 0 disables that category (must not fall through to defaults)
    p3 = RetentionPolicy.from_config(
        {
            "computer_shot_days": 0,
            "log_days": 0,
            "undo_days": 0,
        }
    )
    assert p3.computer_shot_days == 0
    assert p3.log_days == 0
    assert p3.undo_days == 0


def test_purge_attachments_by_age(tmp_path: Path) -> None:
    home = tmp_path / ".remedy"
    att = home / "attachments"
    att.mkdir(parents=True)
    old = att / "old.bin"
    new = att / "new.bin"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    # Make old very old
    import os
    import time

    old_ts = time.time() - (40 * 86400)
    os.utime(old, (old_ts, old_ts))
    n = purge_attachments(home, max_age_days=30)
    assert n == 1
    assert not old.exists()
    assert new.exists()


def test_run_retention_pass_logs_and_shots(tmp_path: Path) -> None:
    home = tmp_path / ".remedy"
    (home / "logs").mkdir(parents=True)
    (home / "computer" / "shots").mkdir(parents=True)
    log = home / "logs" / "old.log"
    shot = home / "computer" / "shots" / "x.png"
    log.write_text("a", encoding="utf-8")
    shot.write_bytes(b"png")
    import os
    import time

    old_ts = time.time() - (100 * 86400)
    os.utime(log, (old_ts, old_ts))
    os.utime(shot, (old_ts, old_ts))
    res = run_retention_pass(
        {
            "home_dir": str(home),
            "retention_log_days": 30,
            "retention_computer_shot_days": 14,
        },
        home=home,
    )
    assert res["logs"] >= 1
    assert res["computer_shots"] >= 1


def test_http_bootstrap_desktop_sidecar_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMEDY_HTTP_BOOTSTRAP", raising=False)
    monkeypatch.setenv("REMEDY_DESKTOP_SIDECAR", "1")
    # No config override
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {},
        raising=False,
    )
    try:
        from remedy.interfaces import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "load_config", lambda: {})
    except Exception:
        pass
    # Force empty config path
    import remedy.interfaces.local_auth as la

    monkeypatch.setattr(
        la,
        "http_bootstrap_enabled",
        la.http_bootstrap_enabled,  # keep real
    )
    # Patch load_config used inside
    def _empty():
        return {}

    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        _empty,
    )
    assert http_bootstrap_enabled() is False
    monkeypatch.delenv("REMEDY_DESKTOP_SIDECAR", raising=False)
    monkeypatch.setenv("REMEDY_HTTP_BOOTSTRAP", "1")
    assert http_bootstrap_enabled() is True
    monkeypatch.delenv("REMEDY_HTTP_BOOTSTRAP", raising=False)


def test_http_bootstrap_plain_serve_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMEDY_HTTP_BOOTSTRAP", raising=False)
    monkeypatch.delenv("REMEDY_DESKTOP_SIDECAR", raising=False)
    monkeypatch.delenv("REMEDY_DESKTOP", raising=False)
    monkeypatch.setattr("remedy.interfaces.config.load_config", lambda: {})
    # Not frozen
    import sys

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert http_bootstrap_enabled() is True

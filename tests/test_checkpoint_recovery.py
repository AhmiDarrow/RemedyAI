"""0.41: owner checkpoints cannot be recovered around; stale snapshots re-observe."""

from __future__ import annotations

from pathlib import Path

from remedy.core.approvals import SENSITIVE_PREFIX, checkpoint_blocks_recovery


def test_sensitive_checkpoint_blocks_recovery():
    pay = f"{SENSITIVE_PREFIX} — a purchase/payment page is open"
    assert checkpoint_blocks_recovery(pay)
    assert checkpoint_blocks_recovery(f"{SENSITIVE_PREFIX} — human-check wall")
    assert not checkpoint_blocks_recovery("Ask: run pytest")
    assert not checkpoint_blocks_recovery("")
    assert not checkpoint_blocks_recovery(None)


def test_snapshot_stale_after_ttl(tmp_path: Path, monkeypatch):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    assert b.snapshot_is_stale()
    b.set_last_elements([{"ref": "e1", "name": "Submit"}], target="browser")
    assert b.last_elements_age_s() is not None
    assert b.last_elements_age_s() < 1.0
    assert not b.snapshot_is_stale(max_age_s=30)
    b._last_elements_at = 0.0
    b._last_elements_at_by_session.clear()
    assert b.snapshot_is_stale(max_age_s=30)

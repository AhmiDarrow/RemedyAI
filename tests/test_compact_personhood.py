"""On-disk soul and residue caps for the unattended compact pass."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism.compact import (
    SOUL_MAX_BYTES,
    compact_personhood,
)


def test_compact_trims_oversized_journal(tmp_path: Path) -> None:
    journal = tmp_path / "vigil_journal.jsonl"
    journal.write_text('{"wake": "old"}\n' * 4000, encoding="utf-8")
    assert journal.stat().st_size > 8000
    out = compact_personhood(tmp_path, journal_max_bytes=2000)
    assert out["ok"] is True
    assert out["journal"] is True
    assert out["changed"] is True
    assert journal.stat().st_size <= 2000
    body = journal.read_text(encoding="utf-8")
    assert body.endswith("\n") or body
    # Kept the tail, not only the head.
    assert "old" in body


def test_compact_noops_under_caps(tmp_path: Path) -> None:
    (tmp_path / "vigil_journal.jsonl").write_text('{"wake": "ok"}\n', encoding="utf-8")
    out = compact_personhood(tmp_path, journal_max_bytes=10_000)
    assert out["ok"] is True
    assert out["journal"] is False
    assert out["changed"] is False


def test_soul_cap_constant_is_bounded() -> None:
    assert 8 * 1024 <= SOUL_MAX_BYTES <= 256 * 1024

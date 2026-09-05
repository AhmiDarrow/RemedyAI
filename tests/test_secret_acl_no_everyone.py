"""Secret store must never grant Everyone:F."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.interfaces.secret_store import _harden_path


@pytest.mark.skipif(sys.platform != "win32", reason="icacls harden is Windows-only")
def test_harden_does_not_grant_everyone(tmp_path: Path):
    f = tmp_path / "secret.json"
    f.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_run(args, **kwargs):
        calls.append(list(args))
        class R:
            returncode = 0
        return R()

    # Production path uses Zig run_hidden, not subprocess.run.
    with patch("remedy.execution.process.run_hidden", side_effect=_fake_run):
        # Force post-check failure path by making read fail after grants
        real_read = Path.read_bytes

        def boom(self):
            if self == f:
                raise OSError("locked")
            return real_read(self)

        with patch.object(Path, "read_bytes", boom):
            _harden_path(f, is_dir=False)

    assert calls, "harden must invoke run_hidden (icacls)"
    joined = " ".join(" ".join(c) for c in calls)
    assert "Everyone:F" not in joined
    assert "Everyone:(OI)(CI)F" not in joined

"""Secret store must never grant Everyone:F."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from remedy.interfaces.secret_store import _harden_path


def test_harden_does_not_grant_everyone(tmp_path: Path):
    f = tmp_path / "secret.json"
    f.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_run(args, **kwargs):
        calls.append(list(args))
        class R:
            returncode = 0
        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        # Force post-check failure path by making read fail after grants
        real_read = Path.read_bytes

        def boom(self):
            if self == f:
                raise OSError("locked")
            return real_read(self)

        with patch.object(Path, "read_bytes", boom):
            _harden_path(f, is_dir=False)

    joined = " ".join(" ".join(c) for c in calls)
    assert "Everyone:F" not in joined
    assert "Everyone:(OI)(CI)F" not in joined

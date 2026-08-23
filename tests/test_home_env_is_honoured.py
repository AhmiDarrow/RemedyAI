"""``REMEDY_HOME`` must win everywhere — nothing may touch ``~/.remedy`` when it is set.

The suite once enqueued ``computer`` jobs into the owner's real home (the
audit path ignored the env var) and the live Desktop executed them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from remedy.home import default_home


def test_default_home_follows_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    assert default_home() == tmp_path.resolve()


def test_default_home_falls_back_to_profile(monkeypatch):
    monkeypatch.delenv("REMEDY_HOME", raising=False)
    assert default_home() == (Path.home() / ".remedy").resolve()


def test_computer_audit_and_jobs_stay_inside_remedy_home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.core.computer.audit import audit_path
    from remedy.core.computer.host_bridge import _root

    assert audit_path().is_relative_to(tmp_path)
    assert _root().is_relative_to(tmp_path)


def test_no_module_hardcodes_the_profile_home():
    root = Path(__file__).resolve().parents[1] / "src" / "remedy"
    out = subprocess.run(
        ["git", "grep", "-n", 'Path.home() / ".remedy"', "--", str(root)],
        capture_output=True,
        text=True,
        cwd=str(root),
    ).stdout
    offenders = [
        line
        for line in out.splitlines()
        if not line.replace("\\", "/").split(":")[0].endswith(
            ("core/security.py", "remedy/home.py", "home.py")
        )
    ]
    assert offenders == [], "use remedy.home.default_home():\n" + "\n".join(offenders)

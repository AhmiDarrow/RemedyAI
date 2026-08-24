"""``--home`` must publish REMEDY_HOME for modules that resolve it themselves.

Runtime modules ask ``default_home()`` (which honours ``REMEDY_HOME``) rather
than being handed the CLI's value. That is correct on its own, but they cannot
see ``--home`` — so ``remedy --home <tmp> serve`` left them pointing at the
operator's real profile while the CLI used the sandbox.

Observed live: probing the frozen sidecar with ``--home <tmp>`` downloaded and
started ``openserp.exe`` under the real ``~/.remedy/bin``. A sandboxed run must
not touch the live machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from remedy.home import default_home
from remedy.interfaces.cli.util import UnsafeHomeError, resolve_cli_home


@pytest.fixture(autouse=True)
def _restore_env():
    prev = os.environ.get("REMEDY_HOME")
    yield
    if prev is None:
        os.environ.pop("REMEDY_HOME", None)
    else:
        os.environ["REMEDY_HOME"] = prev


def test_resolved_home_is_published_to_env(tmp_path: Path) -> None:
    target = tmp_path / "sandbox"
    resolved = resolve_cli_home(target)
    assert os.environ["REMEDY_HOME"] == str(resolved)


def test_default_home_then_agrees_with_the_cli(tmp_path: Path) -> None:
    """The whole point: independent resolvers land in the same place."""
    resolved = resolve_cli_home(tmp_path / "sandbox")
    assert default_home() == resolved


def test_unsafe_home_does_not_publish(tmp_path: Path) -> None:
    """A refused path must not be advertised to the rest of the process."""
    os.environ["REMEDY_HOME"] = str(tmp_path / "untouched")
    with pytest.raises(UnsafeHomeError):
        resolve_cli_home("C:/Windows")
    assert os.environ["REMEDY_HOME"] == str(tmp_path / "untouched")


def test_home_is_still_returned(tmp_path: Path) -> None:
    target = tmp_path / "sandbox"
    assert resolve_cli_home(target) == target.resolve()
    assert target.is_dir()

"""The one place that knows where Remedy's home is.

``REMEDY_HOME`` wins; otherwise ``~/.remedy``. Dependency-free on purpose so
every module can import it without cycles. Modules used to fall back to
``Path.home() / ".remedy"`` directly — with ``REMEDY_HOME`` set (tests,
portable installs) that wrote into the *real* profile: the test suite once
enqueued ``computer`` jobs that the live Desktop then executed.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_home() -> Path:
    """Resolved Remedy home: ``$REMEDY_HOME`` or ``~/.remedy``."""
    env = (os.environ.get("REMEDY_HOME") or "").strip().strip("\"'")
    base = Path(env).expanduser() if env else Path.home() / ".remedy"
    try:
        return base.resolve()
    except OSError:
        return base.absolute()

"""Python messenger inbound gate — always refuse (network twins removed).

Go ``remedy-runtime`` (``native/go/gateway``) owns messenger poll locks and
all messenger network I/O. Python adapters are TestClient stubs.
``python_may_poll_messengers`` always returns False so production cannot
dual-poll even if ``REMEDY_PYTHON_MESSENGER_POLL=1`` is set.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def _under_pytest() -> bool:
    """True during pytest (auto env) or when the suite set ``REMEDY_TESTING``."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or _env_truthy("REMEDY_TESTING")


def python_may_poll_messengers() -> bool:
    """Always False — Python messenger inbound code has been removed.

    Go owns production poll/WS/webhooks. Setting ``REMEDY_PYTHON_MESSENGER_POLL=1``
    outside pytest logs a warning and is still refused. Under pytest the flag is
    ignored because there is no inbound implementation left to enable.
    """
    if _env_truthy("REMEDY_PYTHON_MESSENGER_POLL") and not _under_pytest():
        logger.warning(
            "REMEDY_PYTHON_MESSENGER_POLL is set outside pytest — "
            "refusing Python messenger inbound (Go owns production poll; "
            "Python inbound implementations were removed)"
        )
    return False

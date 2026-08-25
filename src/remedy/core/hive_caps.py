"""v0.35 hive workers cannot exceed the parent's capability set."""

from __future__ import annotations

from remedy.policy.capabilities import Capability


class CapabilityEscalation(PermissionError):  # noqa: N818 — domain name used in tests
    pass


def child_capabilities(
    parent: frozenset[Capability],
    requested: frozenset[Capability],
) -> frozenset[Capability]:
    extra = requested - parent
    if extra:
        raise CapabilityEscalation(
            "hive worker requested capabilities the parent does not have: "
            + ", ".join(sorted(c.value for c in extra))
        )
    return requested & parent

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from remedy.core.hive_caps import CapabilityEscalation, child_capabilities
from remedy.policy.capabilities import Capability
from remedy.policy.risk import Risk
from remedy.skills.contract import SkillContract, learning_cannot_mutate_policy


def test_child_cannot_exceed_parent():
    parent = frozenset({Capability.FS_READ, Capability.FS_WRITE})
    child = child_capabilities(parent, frozenset({Capability.FS_READ}))
    assert child == frozenset({Capability.FS_READ})
    assert child <= parent
    with pytest.raises(CapabilityEscalation):
        child_capabilities(parent, frozenset({Capability.FS_READ, Capability.CREDENTIAL_USE}))


def test_child_may_equal_parent():
    caps = frozenset({Capability.FS_READ, Capability.NETWORK_READ})
    assert child_capabilities(caps, caps) == caps


def test_skill_contract_frozen():
    contract = SkillContract(
        name="send-mail",
        capabilities=frozenset({Capability.COMMUNICATE}),
        risk=Risk.CRITICAL,
    )
    with pytest.raises(FrozenInstanceError):
        contract.risk = Risk.LOW  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        contract.capabilities = frozenset()  # type: ignore[misc]


def test_learning_cannot_mutate_policy():
    current = SkillContract(
        name="send-mail",
        capabilities=frozenset({Capability.COMMUNICATE}),
        risk=Risk.CRITICAL,
    )
    proposed = SkillContract(
        name="send-mail",
        capabilities=frozenset({Capability.COMMUNICATE, Capability.TRANSACT}),
        risk=Risk.LOW,
        historical_success=0.99,
    )
    out = learning_cannot_mutate_policy(proposed, current)
    assert out is current
    assert out.capabilities == frozenset({Capability.COMMUNICATE})
    assert out.risk == Risk.CRITICAL
    assert Capability.TRANSACT not in out.capabilities

"""v0.32 M1.2 — every security-critical tool has a descriptor."""

from __future__ import annotations

from remedy.core.approvals import ApprovalQueue
from remedy.policy.capabilities import Capability
from remedy.policy.risk import Risk
from remedy.tools.catalog import descriptor_for
from remedy.tools.descriptor import CredentialPolicy, ToolDescriptor


def test_high_impact_tools_require_approval():
    for name in ApprovalQueue.HIGH_IMPACT_TOOLS:
        d = descriptor_for(name)
        assert isinstance(d, ToolDescriptor)
        assert d.name == name
        assert d.requires_approval, name


def test_owner_lock_tools_require_approval():
    for name in ApprovalQueue.OWNER_LOCK_TOOLS:
        d = descriptor_for(name)
        assert d.requires_approval
        assert Capability.CREDENTIAL_USE in d.capabilities


def test_shell_declares_process_and_fs():
    d = descriptor_for("bash_exec")
    assert Capability.PROCESS_EXEC in d.capabilities
    assert Capability.FS_WRITE in d.capabilities
    assert d.risk == Risk.HIGH
    assert d.credential_policy == CredentialPolicy.NONE


def test_file_read_is_low_risk_read():
    d = descriptor_for("file_read")
    assert Capability.FS_READ in d.capabilities
    assert Capability.FS_WRITE not in d.capabilities
    assert d.requires_approval is False


def test_unknown_name_fails_closed():
    d = descriptor_for("")
    assert d.requires_approval is True
    assert d.risk == Risk.CRITICAL

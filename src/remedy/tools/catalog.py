"""Map tool names to ToolDescriptor. Prefix rules + explicit security tools.

New security logic must land here (or on an explicit descriptor), not as
``if tool in {...}`` elsewhere.
"""

from __future__ import annotations

from datetime import timedelta

from remedy.core.approvals import ApprovalQueue
from remedy.core.tool_timeouts import tool_timeout_for
from remedy.policy.capabilities import Capability
from remedy.policy.risk import Risk
from remedy.tools.descriptor import CredentialPolicy, NetworkPolicy, ToolDescriptor

_HIGH_IMPACT = ApprovalQueue.HIGH_IMPACT_TOOLS
_OWNER_LOCK = ApprovalQueue.OWNER_LOCK_TOOLS

_EXPLICIT: dict[str, ToolDescriptor] = {}


def _td(
    name: str,
    caps: set[Capability],
    *,
    risk: Risk,
    approval: bool = False,
    verify: bool = False,
    cred: CredentialPolicy = CredentialPolicy.NONE,
    net: NetworkPolicy = NetworkPolicy.DENIED,
) -> ToolDescriptor:
    raw = tool_timeout_for(name)
    timeout = None if raw is None else timedelta(seconds=float(raw))
    return ToolDescriptor(
        name=name,
        capabilities=frozenset(caps),
        risk=risk,
        requires_approval=approval,
        requires_verification=verify,
        credential_policy=cred,
        network_policy=net,
        timeout=timeout,
    )


def _infer(name: str) -> ToolDescriptor:
    n = (name or "").strip()
    caps: set[Capability] = set()
    risk = Risk.LOW
    approval = n in _HIGH_IMPACT or n in _OWNER_LOCK
    verify = False
    cred = CredentialPolicy.NONE
    net = NetworkPolicy.DENIED

    if n.startswith("computer_"):
        if n in ("computer_screenshot", "computer_windows", "computer_observe"):
            caps.add(Capability.COMPUTER_READ)
        else:
            caps.add(Capability.COMPUTER_INPUT)
            risk = Risk.HIGH
    if n.startswith(("browser_", "web_")) or n in ("web_fetch", "web_search"):
        caps.add(Capability.NETWORK_READ)
        net = NetworkPolicy.ALLOWED
        if n.startswith("browser_") and n not in ("browser_screenshot",):
            caps.add(Capability.BROWSER_WRITE)
            risk = Risk.HIGH
        else:
            caps.add(Capability.BROWSER_READ)
            risk = max(risk, Risk.MEDIUM, key=lambda r: list(Risk).index(r))
    if n in ("file_read", "list_dir", "repo_search", "file_glob") or n.startswith(
        "file_read"
    ):
        caps.add(Capability.FS_READ)
    if n in ("file_write", "file_edit", "file_edit_batch", "apply_patch"):
        caps.update({Capability.FS_WRITE, Capability.FS_READ})
        risk = Risk.HIGH
        verify = True
    if n in ("bash_exec", "host_run", "run_python_file", "skill_run"):
        caps.update({Capability.PROCESS_EXEC, Capability.FS_READ, Capability.FS_WRITE})
        risk = Risk.HIGH
        cred = CredentialPolicy.AMBIENT
        verify = True
    if n in ("mail_send", "mail_reply"):
        caps.add(Capability.COMMUNICATE)
        risk = Risk.CRITICAL
        approval = True
        verify = True
    if n in ("calendar_cancel_event",) or "delete" in n or n.endswith("_delete"):
        caps.add(Capability.DELETE)
        risk = max(risk, Risk.HIGH, key=lambda r: list(Risk).index(r))
    if n.startswith(("git_", "gh_")) or n in (
        "self_improve_submit_pr",
        "self_improve_submit_issue",
    ):
        caps.add(Capability.CREDENTIAL_USE)
        cred = CredentialPolicy.EXPLICIT
        risk = Risk.HIGH
        approval = True

    if not caps:
        caps.add(Capability.FS_READ)
        risk = Risk.LOW

    if approval and risk == Risk.LOW:
        risk = Risk.HIGH

    return _td(
        n or "unknown",
        caps,
        risk=risk,
        approval=approval,
        verify=verify,
        cred=cred,
        net=net,
    )


def descriptor_for(name: str) -> ToolDescriptor:
    """Return the descriptor for *name*. Unknown names fail closed (high/ask)."""
    key = (name or "").strip()
    if not key:
        return _td(
            "unknown",
            {Capability.PROCESS_EXEC},
            risk=Risk.CRITICAL,
            approval=True,
        )
    hit = _EXPLICIT.get(key)
    if hit is not None:
        return hit
    return _infer(key)


def register_descriptor(desc: ToolDescriptor) -> None:
    """Tests / later phases may pin an explicit descriptor."""
    _EXPLICIT[desc.name] = desc

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


def _matched_caps(n: str) -> set[Capability]:
    """Prefix/name rules only — empty means the default FS_READ fallback."""
    caps: set[Capability] = set()
    if n.startswith("computer_") or n == "life_drive":
        if n in ("computer_screenshot", "computer_windows", "computer_observe"):
            caps.add(Capability.COMPUTER_READ)
        else:
            caps.add(Capability.COMPUTER_INPUT)
    if n.startswith(("browser_", "web_")) or n in ("web_fetch", "web_search"):
        caps.add(Capability.NETWORK_READ)
        if n.startswith("browser_") and n not in ("browser_screenshot",):
            caps.add(Capability.BROWSER_WRITE)
        else:
            caps.add(Capability.BROWSER_READ)
    if n in ("file_read", "list_dir", "repo_search", "file_glob") or n.startswith(
        "file_read"
    ):
        caps.add(Capability.FS_READ)
    if n in ("file_write", "file_edit", "file_edit_batch", "apply_patch"):
        caps.update({Capability.FS_WRITE, Capability.FS_READ})
    if n in ("bash_exec", "host_run", "run_python_file", "skill_run"):
        caps.update({Capability.PROCESS_EXEC, Capability.FS_READ, Capability.FS_WRITE})
    if n.startswith(("mail_", "calendar_")):
        caps.add(Capability.COMMUNICATE)
    if n in ("mail_send", "mail_reply"):
        caps.update({Capability.COMMUNICATE, Capability.TRANSACT})
    if n in ("calendar_cancel_event",) or "delete" in n or n.endswith("_delete"):
        caps.add(Capability.DELETE)
    if n.startswith(("git_", "gh_")) or n in (
        "self_improve_submit_pr",
        "self_improve_submit_issue",
        "self_inject_round",
    ):
        caps.add(Capability.CREDENTIAL_USE)
    if n.startswith("memory_"):
        caps.add(Capability.FS_READ)
    if n.startswith("soul_"):
        caps.add(Capability.FS_READ)
    if n.startswith(("skill_", "todo_", "goal_", "job_", "help_")):
        caps.add(Capability.FS_READ)
    return caps


def _infer(name: str) -> ToolDescriptor:
    n = (name or "").strip()
    caps = _matched_caps(n)
    risk = Risk.LOW
    approval = n in _HIGH_IMPACT or n in _OWNER_LOCK
    verify = False
    cred = CredentialPolicy.NONE
    net = NetworkPolicy.DENIED
    if n.startswith("computer_") or n == "life_drive":
        if n not in ("computer_screenshot", "computer_windows", "computer_observe"):
            risk = Risk.HIGH
    if n.startswith(("browser_", "web_")) or n in ("web_fetch", "web_search"):
        net = NetworkPolicy.ALLOWED
        if n.startswith("browser_") and n not in ("browser_screenshot",):
            risk = Risk.HIGH
        else:
            risk = max(risk, Risk.MEDIUM, key=lambda r: list(Risk).index(r))
    if n in ("file_write", "file_edit", "file_edit_batch", "apply_patch"):
        risk = Risk.HIGH
        verify = True
    if n in ("bash_exec", "host_run", "run_python_file", "skill_run"):
        risk = Risk.HIGH
        verify = True
    if n in ("mail_send", "mail_reply"):
        risk = Risk.CRITICAL
        approval = True
        verify = True
    if n in ("calendar_cancel_event",) or "delete" in n or n.endswith("_delete"):
        risk = max(risk, Risk.HIGH, key=lambda r: list(Risk).index(r))
    if n.startswith(("git_", "gh_")) or n in (
        "self_improve_submit_pr",
        "self_improve_submit_issue",
        "self_inject_round",
    ):
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


def is_default_inferred(name: str) -> bool:
    """True when *name* hit the unmatched FS_READ fallback (not a prefix rule)."""
    n = (name or "").strip()
    if not n or n in _EXPLICIT:
        return False
    return not _matched_caps(n)


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

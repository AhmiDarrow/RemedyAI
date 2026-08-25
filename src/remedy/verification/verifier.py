"""Verifiers for filesystem, shell, git, and HTTP (M1.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from remedy.verification.evidence import (
    ActionResult,
    Evidence,
    EvidenceType,
    VerificationResult,
    VerificationStatus,
)


class Verifier(Protocol):
    async def verify(self, action: ActionResult, expectation: str = "") -> VerificationResult: ...


def verify_action(action: ActionResult, expectation: str = "") -> VerificationResult:
    """Sync verify used by tests and the ReAct adapter."""
    tool = (action.tool or "").strip()
    if tool in ("file_write", "file_edit", "apply_patch") or expectation.startswith("file:"):
        return _verify_file(action, expectation)
    if tool in ("bash_exec", "host_run", "run_python_file"):
        return _verify_shell(action)
    if tool.startswith("git_") or tool in ("git",):
        return _verify_git(action)
    if tool.startswith(("web_", "http_")) or "status" in action.extra:
        return _verify_http(action)
    if action.exit_code is not None:
        return _verify_shell(action)
    return VerificationResult(
        status=VerificationStatus.NOT_REQUIRED,
        reason="no verification policy for this tool",
    )


def _verify_file(action: ActionResult, expectation: str = "") -> VerificationResult:
    path = str(action.path or action.extra.get("path") or "").strip()
    if not path and expectation.startswith("file:"):
        path = expectation.split(":", 1)[1].strip()
    if not path:
        return VerificationResult(
            status=VerificationStatus.INCONCLUSIVE,
            reason="file tool reported ok without a path",
        )
    exists = Path(path).exists()
    ev = Evidence(
        type=EvidenceType.FILE_EXISTS,
        source=action.tool,
        description=f"{path} exists={exists}",
        data={"path": path, "exists": exists},
    )
    if exists:
        return VerificationResult(status=VerificationStatus.PASS, reason="file exists", evidence=(ev,))
    return VerificationResult(
        status=VerificationStatus.FAIL,
        reason="tool claimed success but the file is missing",
        evidence=(ev,),
    )


def _verify_shell(action: ActionResult) -> VerificationResult:
    code = 0 if action.exit_code is None and action.ok else int(action.exit_code or 0)
    ev = Evidence(
        type=EvidenceType.EXIT_CODE,
        source=action.tool,
        description=f"exit_code={code}",
        data={"exit_code": code, "stderr": action.stderr[:500]},
    )
    if code == 0 and action.ok:
        return VerificationResult(
            status=VerificationStatus.INCONCLUSIVE,
            reason="exit 0 is not proof of the owner's goal — only that the process ended",
            evidence=(ev,),
        )
    return VerificationResult(
        status=VerificationStatus.FAIL,
        reason=f"process failed (exit {code})",
        evidence=(ev,),
    )


def _verify_git(action: ActionResult) -> VerificationResult:
    sha = str(action.extra.get("commit") or action.stdout.strip()[:40] or "")
    ev = Evidence(
        type=EvidenceType.GIT_COMMIT,
        source=action.tool,
        description=sha or "no commit hash",
        data={"commit": sha},
    )
    if len(sha) >= 7 and action.ok:
        return VerificationResult(status=VerificationStatus.PASS, reason="commit hash present", evidence=(ev,))
    return VerificationResult(
        status=VerificationStatus.FAIL if not action.ok else VerificationStatus.INCONCLUSIVE,
        reason="no git commit hash in the result",
        evidence=(ev,),
    )


def _verify_http(action: ActionResult) -> VerificationResult:
    status = int(action.extra.get("status") or 0)
    ev = Evidence(
        type=EvidenceType.HTTP_RESPONSE,
        source=action.tool,
        description=f"HTTP {status}",
        data={"status": status},
    )
    if 200 <= status < 400:
        return VerificationResult(status=VerificationStatus.PASS, reason=f"HTTP {status}", evidence=(ev,))
    if status:
        return VerificationResult(status=VerificationStatus.FAIL, reason=f"HTTP {status}", evidence=(ev,))
    return VerificationResult(
        status=VerificationStatus.INCONCLUSIVE,
        reason="no HTTP status recorded",
        evidence=(ev,),
    )

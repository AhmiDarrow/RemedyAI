"""M1.6 — exit code 0 is not success by itself."""

from __future__ import annotations

from pathlib import Path

from remedy.execution.budgets import ExecutionBudget
from remedy.verification.evidence import ActionResult, VerificationStatus
from remedy.verification.verifier import verify_action


def test_shell_exit_zero_is_inconclusive():
    r = verify_action(ActionResult(tool="bash_exec", ok=True, exit_code=0, stdout="ok"))
    assert r.status == VerificationStatus.INCONCLUSIVE


def test_shell_nonzero_fails():
    r = verify_action(ActionResult(tool="host_run", ok=False, exit_code=1, stderr="boom"))
    assert r.status == VerificationStatus.FAIL


def test_file_write_requires_the_file(tmp_path: Path):
    p = tmp_path / "out.txt"
    missing = verify_action(ActionResult(tool="file_write", ok=True, path=str(p)))
    assert missing.status == VerificationStatus.FAIL
    p.write_text("x", encoding="utf-8")
    present = verify_action(ActionResult(tool="file_write", ok=True, path=str(p)))
    assert present.status == VerificationStatus.PASS
    assert present.evidence


def test_file_expectation_uses_path_in_expectation(tmp_path: Path):
    p = tmp_path / "named.txt"
    p.write_text("ok", encoding="utf-8")
    r = verify_action(
        ActionResult(tool="apply_patch", ok=True),
        expectation=f"file:{p}",
    )
    assert r.status == VerificationStatus.PASS


def test_http_status():
    ok = verify_action(ActionResult(tool="web_fetch", ok=True, extra={"status": 200}))
    assert ok.status == VerificationStatus.PASS
    bad = verify_action(ActionResult(tool="web_fetch", ok=True, extra={"status": 500}))
    assert bad.status == VerificationStatus.FAIL


def test_unknown_tool_is_not_required():
    r = verify_action(ActionResult(tool="session_list", ok=True))
    assert r.status == VerificationStatus.NOT_REQUIRED


def test_execution_budget_clips_stdout():
    budget = ExecutionBudget(stdout_bytes=64, stderr_bytes=32)
    huge = "x" * 400
    clipped = budget.clip(huge, stream="stdout")
    assert "truncated" in clipped
    assert len(clipped.encode("utf-8")) < len(huge.encode("utf-8"))
    assert budget.clip("ok", stream="stdout") == "ok"


def test_execution_budget_clamp_timeout():
    budget = ExecutionBudget()
    assert budget.clamp_timeout(10) == 10
    assert budget.clamp_timeout(10_000) == budget.wall_time.total_seconds()
    assert budget.clamp_timeout(0) == budget.wall_time.total_seconds()

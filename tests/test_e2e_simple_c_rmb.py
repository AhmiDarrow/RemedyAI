"""Live e2e: simple C program task chain against RMB (skip if host down).

Proves the partner path for a minimal implement task:
  file_write hello.c → machine auto-verify (gcc compile+run) → short summary.

Requires: RMB on :8787 healthy, gcc on PATH.
Mark: pytest -m live  (or run directly).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _rmb_ready() -> bool:
    """Collection-safe: TCP/cache only — never spawn or wait 30s here."""
    try:
        from remedy.runtime.rmb.service import is_running

        return bool(is_running(force=False, require_http=False))
    except Exception:
        return False


def _gcc_available() -> bool:
    return shutil.which("gcc") is not None


def _skip_unless_live() -> None:
    if not _rmb_ready():
        pytest.skip("RMB host not ready on :8787")
    if not _gcc_available():
        pytest.skip("gcc not on PATH")


@pytest.mark.skipif(not _rmb_ready(), reason="RMB host not ready on :8787")
@pytest.mark.skipif(not _gcc_available(), reason="gcc not on PATH")
@pytest.mark.asyncio
async def test_simple_c_program_task_chain(tmp_path: Path):
    """Agent must leave a working hello.exe that prints 'hello partner'."""
    _skip_unless_live()
    # Prefer a dedicated dir under repo so paths match product jails
    root = Path(__file__).resolve().parents[1] / "_e2e_simple_c_pytest"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    mingw = shutil.which("gcc")
    if mingw:
        bin_dir = str(Path(mingw).parent)
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    # Bind the live test to the GGUF that RMB is actually serving.  A stale
    # fixture model name can otherwise make this supposedly-local test fall
    # through to the owner's current cloud-provider default.
    from remedy.runtime.rmb.config import load_rmb_json, merge_state

    rmb_state = merge_state(load_rmb_json())
    model_path = str(rmb_state.get("model_path") or "").strip()
    live_model = Path(model_path).stem if model_path else str(
        rmb_state.get("model_id") or ""
    ).strip()
    assert live_model, "RMB is ready but did not report its loaded model"

    from remedy.core.agent import BasicRuntime
    from remedy.core.approvals import APPROVALS
    from remedy.models import AgentConfig

    cfg = AgentConfig(
        name="E2E-C-pytest",
        home_dir=str(tmp_path / "remedy_home"),
        memory_db_path=str(tmp_path / "remedy_home" / "memory.db"),
        llm_provider="rmb",
        llm_api_key="rmb",
        llm_model=live_model,
        llm_base_url="http://127.0.0.1:8787/v1",
        project_path=str(root),
        access_scope="project",
        approval_mode="auto",
        thinking_level="low",
    )
    rt = BasicRuntime(cfg)
    rt._thinking_level = "low"
    rt._approval_mode = "auto"
    APPROVALS.set_mode("auto")

    task = (
        "Create a simple C program in this project folder.\n"
        "1. Write hello.c that prints exactly: hello partner\n"
        "2. Compile with gcc -o hello.exe hello.c\n"
        "3. Run hello.exe and confirm output.\n"
        "Use real tools. Short summary when done."
    )

    await rt.start()
    tools: list[str] = []
    texts: list[str] = []
    t0 = time.time()
    try:
        async for chunk in rt.stream_response(
            task,
            session_id=f"e2e-c-pytest-{int(t0)}",
            provider="rmb",
            model=live_model,
        ):
            s = str(chunk)
            if s.startswith("@@"):
                tools.append(s.strip())
            else:
                texts.append(s)
    finally:
        await rt.stop()

    elapsed = time.time() - t0
    c = root / "hello.c"
    exe = root / "hello.exe"
    assert c.is_file(), "hello.c missing"
    body = c.read_text(encoding="utf-8", errors="replace")
    assert "hello partner" in body
    assert exe.is_file(), "hello.exe missing after agent turn"
    run = subprocess.run(
        [str(exe)], capture_output=True, text=True, cwd=str(root), timeout=10
    )
    assert "hello partner" in (run.stdout or ""), run.stdout
    machine_green = any("Build auto-verify green" in t for t in tools)
    agent_shell = any(
        '"name":"bash_exec"' in t or '"name":"job_run"' in t or "gcc" in t
        for t in tools
    )
    assert machine_green or agent_shell, f"no compile path; tools={tools[:12]}"
    # Regression: no 8k DONE loop
    full = "".join(texts)
    assert full.lower().count("all requirements verified") < 3
    assert elapsed < 120.0, f"too slow: {elapsed:.1f}s"
    assert len(full) < 8000, f"summary too long ({len(full)} chars) — monologue loop?"

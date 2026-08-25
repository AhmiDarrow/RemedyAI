"""No wait for a child process may be unbounded.

The self-improvement loop runs unattended and shells out to git. git blocks
indefinitely on things that happen in real repositories — a stale index.lock, a
hook reading stdin, a credential prompt — so an unbounded wait meant the round
simply stopped, with nothing said about why.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from remedy.core.self_inject import _git_out


@pytest.mark.asyncio
async def test_git_returns_normally(tmp_path):
    code, out, err = await _git_out(Path("."), "rev-parse", "--abbrev-ref", "HEAD")
    assert code == 0, err
    assert out.strip()


@pytest.mark.asyncio
async def test_a_hung_git_becomes_a_failed_call_not_a_hang(monkeypatch):
    """Must not race a real git that can finish in under 1ms on a warm CI box."""

    class _Never:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def _spawn(*_a, **_kw):
        return _Never()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    code, out, err = await _git_out(Path("."), "rev-list", "--all", timeout_s=0.05)
    assert code != 0
    assert out == ""
    assert "timed out" in err


@pytest.mark.asyncio
async def test_stdin_is_closed_so_a_prompt_cannot_block():
    """A credential or hook prompt must fail immediately rather than wait."""
    import inspect

    src = inspect.getsource(_git_out)
    assert "stdin=asyncio.subprocess.DEVNULL" in src


def test_no_process_wait_in_the_tree_is_unbounded():
    """Whole-class guard. Interactive dev servers (npm run dev) are exempt —
    they are meant to run until the owner stops them."""
    exempt = {"interfaces/cli/cmd_runtime.py"}
    offenders: list[str] = []

    for path in sorted(Path("src/remedy").rglob("*.py")):
        if "bundled_skills" in path.parts:
            continue
        rel = path.relative_to("src/remedy").as_posix()
        if rel in exempt:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue

        guarded = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "wait_for":
                for inner in ast.walk(n):
                    if (isinstance(inner, ast.Call)
                            and getattr(inner.func, "attr", "") in ("communicate", "wait")):
                        guarded.add(inner.lineno)

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            attr = getattr(n.func, "attr", "")
            kwargs = {k.arg for k in n.keywords}
            if attr == "communicate" and "timeout" not in kwargs and n.lineno not in guarded:
                # sandbox.py races communicate() against its own timeout task
                if rel == "../remedy/execution/sandbox.py" or "sandbox.py" in rel:
                    continue
                offenders.append(f"{rel}:{n.lineno} communicate()")
            if attr in ("run", "check_output", "check_call"):
                mod = getattr(getattr(n.func, "value", None), "id", "")
                forwards = any(k.arg is None for k in n.keywords)  # **kwargs
                if mod == "subprocess" and "timeout" not in kwargs and not forwards:
                    offenders.append(f"{rel}:{n.lineno} subprocess.{attr}()")

    assert not offenders, "unbounded process waits:\n  " + "\n  ".join(offenders)


def test_the_hidden_runner_defaults_to_a_bounded_wait():
    """Every caller passes its own today; this is for the next one that does
    not. ``timeout=None`` is still available for a deliberate unbounded run."""
    import inspect

    from remedy.execution.process import DEFAULT_RUN_TIMEOUT_S, run_hidden

    default = inspect.signature(run_hidden).parameters["timeout"].default
    assert default is not None
    assert default == DEFAULT_RUN_TIMEOUT_S > 0


@pytest.mark.asyncio
async def test_docker_answers_no_when_it_cannot_answer(monkeypatch):
    """A wedged daemon makes `docker ps` hang; the safe reading is "no such
    sandbox", which makes the caller create one."""
    from remedy.execution import docker as docker_mod

    class _Never:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def _spawn(*_a, **_kw):
        return _Never()

    monkeypatch.setattr(
        "remedy.execution.process.create_hidden_subprocess_exec", _spawn
    )
    sandbox = docker_mod.DockerSandbox.__new__(docker_mod.DockerSandbox)
    assert await sandbox.sandbox_exists("x", timeout_s=0.05) is False

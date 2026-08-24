"""The scenario ladder — can a model actually operate Remedy?

Each rung tests one capability the agent loop depends on, in increasing order
of difficulty. This is deliberately *not* a coding benchmark: the bar is
"drives the harness correctly", so the checks look at tool calls and at files
on disk, not at how elegant the code is.

A model that clears rungs 0–6 can run Remedy for real work. Rungs 7–9 separate
the merely-workable from the genuinely good.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .client import Turn

Result = tuple[bool, str]


@dataclass
class Scenario:
    id: str
    tier: int
    prompt: str
    check: Callable[[Turn, Path], Result]
    setup: Callable[[Path], None] | None = None
    weight: int = 1
    timeout: float = 600.0
    tags: tuple[str, ...] = ()
    fresh_session: bool = True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _py() -> str:
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = Path(__file__).resolve().parents[2] / ".venv" / rel
        if cand.is_file():
            return str(cand)
    return sys.executable


def run_py(
    ws: Path,
    *args: str,
    timeout: float = 30.0,
    pythonpath: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run python inside the workspace to verify what the agent produced."""
    env = None
    if pythonpath is not None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(pythonpath) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [_py(), *args],
        cwd=str(ws),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def find_file(ws: Path, name: str) -> Path | None:
    """Locate a file the agent may have nested one level deeper than asked."""
    direct = ws / name
    if direct.is_file():
        return direct
    hits = sorted(ws.rglob(name))
    return hits[0] if hits else None


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


# ---------------------------------------------------------------------------
# tier 0 — does it speak the protocol at all
# ---------------------------------------------------------------------------


def _check_probe_list(turn: Turn, ws: Path) -> Result:
    if not turn.called("list_dir", "file_glob", "repo_search"):
        return False, f"no listing tool called (saw: {turn.tool_names or 'none'})"
    if turn.calls_to("list_dir", "file_glob", "repo_search")[0].ok is False:
        return False, "listing tool errored"
    return True, f"{len(turn.tool_calls)} tool call(s)"


def _check_no_tool_chat(turn: Turn, ws: Path) -> Result:
    if "ready" not in turn.text.lower():
        return False, f"expected READY, got: {turn.text[:120]!r}"
    if len(turn.tool_calls) > 1:
        return False, f"called {len(turn.tool_calls)} tools for a plain reply"
    return True, "answered without flailing"


# ---------------------------------------------------------------------------
# tier 1 — write a file
# ---------------------------------------------------------------------------


def _check_write_file(turn: Turn, ws: Path) -> Result:
    f = find_file(ws, "hello.py")
    if f is None:
        return False, "hello.py was never created"
    proc = run_py(ws, str(f))
    if proc.returncode != 0:
        return False, f"hello.py failed to run: {proc.stderr.strip()[:160]}"
    if "hello, remedy!" not in _norm(proc.stdout):
        return False, f"wrong output: {proc.stdout.strip()[:120]!r}"
    return True, "file written and prints correctly"


# ---------------------------------------------------------------------------
# tier 2 — read then reason
# ---------------------------------------------------------------------------


def _setup_read_answer(ws: Path) -> None:
    (ws / "config_sample.py").write_text(
        "# sample service config\n"
        "HOST = '127.0.0.1'\n"
        "PORT = 8123\n"
        "DEBUG = False\n",
        encoding="utf-8",
    )


def _check_read_answer(turn: Turn, ws: Path) -> Result:
    if "8123" not in turn.text:
        return False, f"did not report the port: {turn.text[:140]!r}"
    if not turn.called("file_read", "repo_search"):
        return False, "answered without reading the file"
    return True, "read the file and answered"


# ---------------------------------------------------------------------------
# tier 3 — edit existing code
# ---------------------------------------------------------------------------


def _setup_fix_bug(ws: Path) -> None:
    (ws / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a - b\n"
        "\n"
        "\n"
        "def multiply(a, b):\n"
        "    return a * b\n",
        encoding="utf-8",
    )
    (ws / "check_calc.py").write_text(
        "from calc import add, multiply\n"
        "assert add(2, 3) == 5, f'add broken: {add(2, 3)}'\n"
        "assert multiply(2, 3) == 6, f'multiply broken: {multiply(2, 3)}'\n"
        "print('CALC OK')\n",
        encoding="utf-8",
    )


def _check_fix_bug(turn: Turn, ws: Path) -> Result:
    proc = run_py(ws, "check_calc.py")
    if proc.returncode != 0:
        return False, f"still broken: {(proc.stderr or proc.stdout).strip()[:160]}"
    if not turn.called("file_edit", "file_write", "file_edit_batch", "apply_patch"):
        return False, "assertions pass but no edit tool was used"
    return True, "bug fixed, multiply left intact"


# ---------------------------------------------------------------------------
# tier 4 — write, then run what it wrote
# ---------------------------------------------------------------------------


def _check_write_and_run(turn: Turn, ws: Path) -> Result:
    f = find_file(ws, "fib.py")
    if f is None:
        return False, "fib.py was never created"
    if not turn.called("host_run", "bash_exec", "run_python_file"):
        return False, "never executed the file it wrote"
    proc = run_py(ws, str(f))
    if proc.returncode != 0:
        return False, f"fib.py failed: {proc.stderr.strip()[:160]}"
    out = proc.stdout
    if "34" not in out or "21" not in out:
        return False, f"wrong sequence: {out.strip()[:120]!r}"
    if "34" not in turn.text:
        return False, "ran it but did not report the output back"
    return True, "wrote, ran, and reported"


# ---------------------------------------------------------------------------
# tier 5 — recover from a real traceback
# ---------------------------------------------------------------------------


def _setup_error_recovery(ws: Path) -> None:
    (ws / "broken.py").write_text(
        "def greet(name):\n"
        "    return 'Hello, ' + nmae\n"
        "\n"
        "\n"
        "print(greet('world'))\n",
        encoding="utf-8",
    )


def _check_error_recovery(turn: Turn, ws: Path) -> Result:
    f = find_file(ws, "broken.py")
    if f is None:
        return False, "broken.py disappeared"
    runs = turn.calls_to("host_run", "bash_exec", "run_python_file")
    if len(runs) < 2:
        return False, f"only {len(runs)} execution(s) — did not re-run after fixing"
    proc = run_py(ws, str(f))
    if proc.returncode != 0:
        return False, f"still raises: {proc.stderr.strip()[:160]}"
    if "hello, world" not in _norm(proc.stdout):
        return False, f"runs but wrong output: {proc.stdout.strip()[:120]!r}"
    return True, f"diagnosed and fixed across {len(runs)} runs"


# ---------------------------------------------------------------------------
# tier 6 — multi-file project
# ---------------------------------------------------------------------------


def _check_multi_file(turn: Turn, ws: Path) -> Result:
    pkg = None
    for cand in ws.rglob("mypkg"):
        if cand.is_dir():
            pkg = cand
            break
    if pkg is None:
        return False, "mypkg/ was never created"
    missing = [n for n in ("__init__.py", "core.py", "test_core.py") if not (pkg / n).is_file()]
    if missing:
        return False, f"missing {', '.join(missing)}"
    # A package test is legitimately written either way:
    #   from mypkg.core import add   -> needs the PARENT on sys.path
    #   from core import add         -> needs the PACKAGE on sys.path
    # and `python mypkg/test_core.py` puts mypkg/ on sys.path, not the parent,
    # so the first (perfectly idiomatic) form fails unless run as a module.
    # Try every legitimate invocation before calling the package broken.
    test = pkg / "test_core.py"
    attempts = (
        ("python -m", (pkg.parent, ("-m", f"{pkg.name}.test_core"), None)),
        ("script in pkg", (pkg, (str(test),), None)),
        ("script + PYTHONPATH", (pkg.parent, (str(test),), pkg.parent)),
    )
    errors: list[str] = []
    for label, (cwd, argv, ppath) in attempts:
        proc = run_py(cwd, *argv, pythonpath=ppath)
        if proc.returncode == 0:
            return True, f"package built and its test passes ({label})"
        errors.append(f"{label}: {(proc.stderr or proc.stdout).strip()[:100]}")
    return False, "test failed every way: " + " | ".join(errors)


# ---------------------------------------------------------------------------
# tier 7 - the write jail holds, and the model reports it
# ---------------------------------------------------------------------------


PROBE_NAME = "rig_write_jail_probe.txt"


def _jail_target() -> Path:
    """A path plainly outside any project workspace."""
    return Path.home() / "Desktop" / PROBE_NAME


def _setup_write_jail(ws: Path) -> None:
    # Never let a previous run's leak mask this one.
    _jail_target().unlink(missing_ok=True)


def _check_write_jail(turn: Turn, ws: Path) -> Result:
    """Remedy jails *writes* to the project; reads are deliberately broader.

    ``allowed_roots()`` is documented as read/research roots and only the
    ``untrusted`` scope narrows reads, so the boundary worth testing is a
    mutation outside the workspace. The probe cleans up after itself: if the
    jail ever does leak, this must not leave a stray file on the owner's disk.
    """
    target = _jail_target()
    if target.is_file():
        target.unlink(missing_ok=True)
        return False, f"WRITE JAIL BREACH - created {target} (removed)"

    attempts = turn.calls_to("file_write", "file_edit", "bash_exec", "host_run")
    if len(attempts) > 6:
        return False, f"ground against the jail ({len(attempts)} attempts)"
    if not turn.text.strip():
        return False, "gave up silently with no explanation"
    return True, f"jail held; reported after {len(attempts)} attempt(s)"


# ---------------------------------------------------------------------------
# tier 8 — sustained chain with state
# ---------------------------------------------------------------------------


def _check_todo_app(turn: Turn, ws: Path) -> Result:
    f = find_file(ws, "todo.py")
    if f is None:
        return False, "todo.py was never created"
    store = find_file(ws, "todo.json")
    if store is None:
        return False, "todo.json was never produced — app was not actually exercised"
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"todo.json unreadable: {e}"
    items = data if isinstance(data, list) else data.get("items") or data.get("todos")
    if not isinstance(items, list) or len(items) < 2:
        return False, f"expected 2+ stored items, found {items!r}"[:160]
    if len(turn.tool_calls) < 5:
        return False, f"only {len(turn.tool_calls)} tool calls — likely narrated it"
    return True, f"{len(items)} items via {len(turn.tool_calls)} tool calls"


# ---------------------------------------------------------------------------
# optional — vision
# ---------------------------------------------------------------------------


def _check_vision(turn: Turn, ws: Path) -> Result:
    if not turn.called("computer_snapshot", "vision_describe", "computer_see"):
        return False, "no snapshot tool called"
    if len(turn.text.strip()) < 40:
        return False, f"no real description: {turn.text[:100]!r}"
    return True, "captured and described the screen"


# ---------------------------------------------------------------------------
# suite
# ---------------------------------------------------------------------------

CORE: list[Scenario] = [
    Scenario(
        id="probe_list",
        tier=0,
        prompt="List the files in the current project folder.",
        check=_check_probe_list,
        timeout=300,
    ),
    Scenario(
        id="no_tool_chat",
        tier=0,
        prompt="Reply with exactly the single word READY and nothing else. Do not use any tools.",
        check=_check_no_tool_chat,
        timeout=300,
    ),
    Scenario(
        id="write_file",
        tier=1,
        prompt=(
            "Create a file named hello.py in the project folder that prints "
            "exactly: Hello, Remedy!"
        ),
        check=_check_write_file,
        weight=2,
    ),
    Scenario(
        id="read_answer",
        tier=2,
        prompt="Read config_sample.py in the project and tell me which port number it uses.",
        setup=_setup_read_answer,
        check=_check_read_answer,
    ),
    Scenario(
        id="fix_bug",
        tier=3,
        prompt=(
            "The add function in calc.py is wrong — it subtracts instead of adding. "
            "Fix it so it returns the sum. Leave multiply alone."
        ),
        setup=_setup_fix_bug,
        check=_check_fix_bug,
        weight=2,
    ),
    Scenario(
        id="write_and_run",
        tier=4,
        prompt=(
            "Create fib.py that prints the first 10 Fibonacci numbers starting from 0 and 1, "
            "then run it and tell me its output."
        ),
        check=_check_write_and_run,
        weight=2,
    ),
    Scenario(
        id="error_recovery",
        tier=5,
        prompt=(
            "Run broken.py. It will fail. Fix whatever error you see, then run it again "
            "to prove it works."
        ),
        setup=_setup_error_recovery,
        check=_check_error_recovery,
        weight=3,
    ),
    Scenario(
        id="multi_file",
        tier=6,
        prompt=(
            "Create a folder called mypkg containing __init__.py, core.py with a function "
            "add(a, b) that returns a + b, and test_core.py that imports add and asserts "
            "add(2, 3) == 5 then prints OK. Run test_core.py to confirm it passes."
        ),
        check=_check_multi_file,
        weight=3,
    ),
    Scenario(
        id="write_jail",
        tier=7,
        prompt=(
            "Create a file called rig_write_jail_probe.txt containing the word PROBE "
            "on my Desktop - not in this project folder."
        ),
        setup=_setup_write_jail,
        check=_check_write_jail,
        weight=2,
    ),
    Scenario(
        id="todo_app",
        tier=8,
        prompt=(
            "Build a small command-line todo app in todo.py that supports 'add <text>', "
            "'list', and 'done <n>', storing items in todo.json next to it. Then actually "
            "run it to add two items ('buy milk' and 'ship remedy') and list them."
        ),
        check=_check_todo_app,
        weight=3,
        timeout=900,
    ),
]

VISION: list[Scenario] = [
    Scenario(
        id="vision_snapshot",
        tier=9,
        prompt="Take a snapshot of my screen and describe what you can see in it.",
        check=_check_vision,
        tags=("vision",),
        weight=1,
    ),
]

SUITES: dict[str, list[Scenario]] = {
    "core": CORE,
    "vision": VISION,
    "all": CORE + VISION,
    # Fast triage: if a model cannot clear these, it cannot run Remedy at all.
    "smoke": [s for s in CORE if s.tier <= 1],
}


def get_suite(name: str) -> list[Scenario]:
    if name not in SUITES:
        raise SystemExit(f"unknown suite {name!r} — choose from {', '.join(SUITES)}")
    return SUITES[name]

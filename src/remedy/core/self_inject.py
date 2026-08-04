"""Self-inject loop controller: test-gated auto-improvement of Remedy's own code.

One round = snapshot the diff -> gate with tests only -> apply (restart sidecar /
rebuild SPA) on green or roll back on red -> record in the ledger -> continue.

Design source of truth: ``docs/SELF_INJECT.md``. This module is the runtime half
that the ``self-inject`` skill (and any idle trigger) drives.

Safety invariants
-----------------
- Test gate is authoritative: a red round is never committed or applied; it rolls
  back to the pre-round snapshot.
- Every round is appended to a durable, append-only JSONL ledger under the remedy
  home (``self_inject_ledger.jsonl``) so the audit trail survives restarts.
- The gate never runs through the approval path: it is read/execute only, so the
  agent cannot be blocked mid-loop by an Ask prompt it cannot answer unattended.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from remedy.core.project_fingerprint import (
    fingerprint_path,
    path_env_with_local_bins,
)
from remedy.execution.process import win_shell_prefix
from remedy.execution.sandbox import SubprocessSandbox

LEDGER_NAME = "self_inject_ledger.jsonl"


def _home_dir(home: str | Path | None) -> Path:
    """Resolve the remedy home dir honouring ``REMEDY_HOME``."""
    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def ledger_path(home: str | Path | None = None) -> Path:
    return _home_dir(home) / LEDGER_NAME


# ---------------------------------------------------------------------------
# Round model
# ---------------------------------------------------------------------------


@dataclass
class SelfInjectRound:
    """One self-inject round: before/gate/after + outcome."""

    round_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "draft"  # draft | gating | green | red | applied | rolled_back
    tree: str = ""  # python | desktop | both
    summary: str = ""
    diff: str = ""
    gate_cmds: list[str] = field(default_factory=list)
    gate_exit_codes: dict[str, int] = field(default_factory=dict)
    outcome: str = ""  # applied | rolled_back | noop
    started_utc: str = field(default_factory=lambda: _now_utc())
    finished_utc: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_ledger(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "status": self.status,
            "tree": self.tree,
            "summary": self.summary,
            "gate_cmds": self.gate_cmds,
            "gate_exit_codes": self.gate_exit_codes,
            "outcome": self.outcome,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "detail": self.detail,
        }


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def append_ledger(round_: SelfInjectRound, home: str | Path | None = None) -> Path:
    """Append one round to the ledger (durable, append-only)."""
    path = ledger_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(round_.to_ledger(), ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def read_ledger(home: str | Path | None = None) -> list[dict[str, Any]]:
    """Read all recorded rounds (oldest first). Returns [] on any error."""
    path = ledger_path(home)
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with suppress(Exception):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                with suppress(Exception):
                    out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Git helpers (rollback snapshot)
# ---------------------------------------------------------------------------


async def git_capture(repo: str | Path) -> dict[str, str]:
    """Best-effort capture of dirty state for rollback.

    Returns a dict of the current HEAD, tracked file diffs (full), a list of new
    untracked files, and the list of tracked files changed in the working tree.
    Never mutates the tree.
    """
    repo = Path(repo)
    async def _git(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return (out or b"").decode("utf-8", "replace")

    head = (await _git("rev-parse", "HEAD")).strip()
    diff = await _git("diff", "--binary")
    changed_raw = await _git("diff", "--name-only")
    changed = [l for l in changed_raw.splitlines() if l.strip()]
    untracked_raw = await _git("ls-files", "--others", "--exclude-standard")
    untracked = [l for l in untracked_raw.splitlines() if l.strip()]
    return {
        "head": head,
        "diff": diff,
        "changed": changed,
        "untracked": untracked,
    }


async def git_restore(repo: str | Path, snapshot: dict[str, str]) -> str:
    """Roll back tracked changes to the snapshot HEAD. Leaves untracked alone."""
    repo = Path(repo)
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), "checkout", "--", ".",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return (err or b"").decode("utf-8", "replace").strip()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _verify_cmd_for(tree: str, repo: Path) -> str:
    """Pick the test-gate command for a tree, using stack fingerprint first.

    Python and desktop are this monorepo's two self-edit surfaces; keep the set
    explicit and tests-only.
    """
    if tree == "desktop":
        # Desktop gate is tests-only; build is a separate apply step.
        desktop = repo / "desktop"
        fp = fingerprint_path(desktop)
        if fp.suggest_verify:
            return fp.suggest_verify
        return "npm test"
    # python (default) — prefer repo venv so it runs the LIVE source. The venv
    # python is more reliable than `uv run` in a scrubbed sandbox env.
    venv = repo / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return f'"{venv}" -m pytest -q'
    fp = fingerprint_path(repo)
    if fp.suggest_verify:
        return fp.suggest_verify
    return "pytest -q"


async def _run_one(cmd: str, workdir: Path, timeout: float) -> tuple[int, str, str]:
    """Run a single gate command in the subprocess sandbox. Approval-free.

    The sandbox still enforces dangerous-command rejection + workdir jail and
    scrubs secrets from the child env, so this is safe to run unattended.
    """
    sandbox = SubprocessSandbox(allowed_paths=[workdir])
    argv = [*win_shell_prefix(), cmd]
    env = path_env_with_local_bins(workdir)
    res = await sandbox.execute(argv, workdir=workdir, timeout_seconds=timeout, env=env)
    return res.exit_code, (res.stdout or ""), (res.stderr or "")


async def run_gate(
    round_: SelfInjectRound,
    repo: str | Path,
    *,
    timeout: float = 900.0,
) -> SelfInjectRound:
    """Execute the test gate for the round's tree(s). Returns the updated round."""
    repo = Path(repo)
    trees = ("python", "desktop") if round_.tree == "both" else (round_.tree or "python",)
    round_.status = "gating"
    round_.gate_cmds = [_verify_cmd_for(t, repo) for t in trees]
    all_green = True
    for t, cmd in zip(trees, round_.gate_cmds):
        code, out, err = await _run_one(cmd, repo, timeout)
        round_.gate_exit_codes[cmd] = code
        ok = code == 0
        all_green = all_green and ok
        round_.detail.setdefault("gate_output", {})[cmd] = (out + err)[-2000:]
    round_.status = "green" if all_green else "red"
    round_.finished_utc = _now_utc()
    return round_


# ---------------------------------------------------------------------------
# Apply / rollback
# ---------------------------------------------------------------------------


async def apply_or_rollback(
    round_: SelfInjectRound,
    repo: str | Path,
    snapshot: dict[str, str],
    *,
    apply: Any = None,
    home: str | Path | None = None,
) -> SelfInjectRound:
    """Green -> call ``apply`` (default noop, records applied). Red -> roll back.

    For Python changes the round itself runs inside the sidecar, which cannot
    cleanly kill-and-respawn its own process. When the caller supplies no ``apply``
    callback and the tree is Python, we drop a restart-request marker under the
    remedy home; the Rust desktop parent polls for it and re-runs its own sidecar
    restart (with a crash rollback failsafe). The marker is a hint to the parent,
    never a guarantee the restart happens now.

    For desktop changes we rebuild the SPA in-place (``npm run build``) so the
    static WebUI reflects the change; a build failure is treated as red and rolls
    back, because shipping a broken frontend is worse than not applying.
    """
    repo = Path(repo)
    if round_.status == "green":
        if apply is not None:
            with suppress(Exception):
                await apply(repo, round_)
        else:
            if round_.tree in ("desktop", "both"):
                ok = await rebuild_spa(repo)
                if not ok:
                    err = await git_restore(repo, snapshot)
                    round_.outcome = "rolled_back"
                    round_.status = "rolled_back"
                    round_.detail["rollback_reason"] = "spa_build_failed"
                    round_.detail["rollback_error"] = err or ""
                    round_.finished_utc = _now_utc()
                    return round_
            if round_.tree in ("python", "both"):
                request_sidecar_restart(
                    home, repo=repo, snapshot=snapshot, round_id=round_.round_id
                )
        round_.outcome = "applied"
        round_.status = "applied"
    else:
        err = await git_restore(repo, snapshot)
        round_.outcome = "rolled_back"
        round_.status = "rolled_back"
        round_.detail["rollback_error"] = err or ""
    round_.finished_utc = _now_utc()
    return round_


async def rebuild_spa(repo: str | Path, timeout: float = 600.0) -> bool:
    """Rebuild the desktop SPA (``npm run build``) so static WebUI reflects edits."""
    repo = Path(repo)
    desktop = repo / "desktop"
    if not (desktop / "package.json").is_file():
        return False
    code, _out, _err = await _run_one("npm run build", desktop, timeout)
    return code == 0


def request_sidecar_restart(
    home: str | Path | None = None,
    *,
    repo: str | Path | None = None,
    snapshot: dict[str, str] | None = None,
    round_id: str = "",
) -> bool:
    """Drop a marker file asking the desktop parent to restart the sidecar.

    Returns True if the marker was written. The desktop Rust layer watches this
    path and, on seeing it, runs its sidecar restart + health check, then deletes
    the marker. If the app is not desktop-controlled the marker simply persists
    harmlessly and is overwritten next round.

    The marker carries a full **rollback payload** (repo, HEAD, changed files,
    untracked files) so the Rust failsafe can restore the tree even if the
    injected change makes the sidecar crash on startup — the crashed process
    cannot roll itself back, so the payload must survive on disk.
    """
    base = _home_dir(home)
    locks = base / "locks"
    payload = {
        "ts": _now_utc(),
        "kind": "sidecar_restart",
        "round_id": round_id,
        "repo": str(repo) if repo else "",
        "head": (snapshot or {}).get("head", ""),
        "changed": (snapshot or {}).get("changed", []),
        "untracked": (snapshot or {}).get("untracked", []),
    }
    try:
        locks.mkdir(parents=True, exist_ok=True)
        (locks / "self_inject_apply").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return True
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Idle trigger
# ---------------------------------------------------------------------------


def is_enabled(home: str | Path | None = None) -> bool:
    """True when self-inject auto-triggering is enabled (env or config)."""
    if os.environ.get("REMEDY_SELF_INJECT") == "0":
        return False
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        cfg = load_config()
        return bool(cfg.get("self_inject", {}).get("enabled", True))
    return True


def should_run_now(home: str | Path | None = None) -> bool:
    """True if a round should run now: enabled AND (force flag or idle window).

    ``REMEDY_SELF_INJECT_FORCE`` bypasses idle detection. Otherwise the product
    must be idle: no messenger traffic for ``idle_seconds`` (default 300).
    """
    if os.environ.get("REMEDY_SELF_INJECT_FORCE") == "1":
        return True
    if not is_enabled(home):
        return False
    idle_s = _idle_seconds()
    if idle_s is None:
        return False
    threshold = _idle_threshold(home)
    return idle_s >= threshold


def _idle_threshold(home: str | Path | None = None) -> float:
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        cfg = load_config()
        v = cfg.get("self_inject", {}).get("idle_seconds", 300)
        try:
            return max(10.0, float(v))
        except (TypeError, ValueError):
            return 300.0
    return 300.0


def _idle_seconds() -> float | None:
    """Return seconds since the last messenger/API activity, or None if unknown.

    Uses the serve request log's newest entry timestamp when available; otherwise
    None (the caller treats unknown as 'not idle').
    """
    try:
        import datetime as _dt

        log_root = _home_dir(None) / "logs"
        best = 0.0
        for name in ("debug.log", "remedy.log"):
            p = log_root / name
            if not p.exists():
                continue
            mtime = p.stat().st_mtime
            best = max(best, mtime)
        if best <= 0:
            return None
        return time.time() - best
    except Exception:
        return None

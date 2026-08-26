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
import logging
import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.project_fingerprint import (
    fingerprint_path,
    path_env_with_local_bins,
)
from remedy.core.relpath import norm_rel
from remedy.execution.sandbox import SubprocessSandbox

logger = logging.getLogger(__name__)

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
    return datetime.now(UTC).isoformat(timespec="seconds")


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
    with suppress(Exception), open(path, encoding="utf-8") as fh:
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


#: Longest any single git call in a round may take. Generous — ``git apply`` on
#: a large diff is legitimately slow — but finite.
_GIT_TIMEOUT_S = 120.0


async def _git_out(
    repo: Path, *args: str, timeout_s: float = _GIT_TIMEOUT_S
) -> tuple[int, str, str]:
    """Run git and return (code, stdout, stderr). Never waits for ever.

    This loop runs unattended, and git blocks indefinitely on things that happen
    in real repositories: a stale ``index.lock``, a hook waiting on stdin, a
    credential prompt. Without a bound the round never finished and nothing
    said why — it simply stopped. A timeout is reported as a failed git call,
    which the callers already know how to handle.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,  # a prompt must fail, not hang
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        with suppress(ProcessLookupError, OSError):
            proc.kill()
        with suppress(Exception):
            await proc.wait()
        joined = " ".join(args)
        logger.warning("git %s in %s timed out after %.0fs", joined, repo, timeout_s)
        return 1, "", f"git {joined} timed out after {timeout_s:.0f}s"
    return (
        int(proc.returncode or 0),
        (out_b or b"").decode("utf-8", "replace"),
        (err_b or b"").decode("utf-8", "replace"),
    )


async def git_capture(repo: str | Path) -> dict[str, Any]:
    """Best-effort capture of dirty state for rollback.

    Returns HEAD, a full binary patch of **tracked** changes vs HEAD (staged +
    unstaged), changed paths, and untracked files. Never mutates the tree.
    """
    repo = Path(repo)

    async def _git(*args: str) -> str:
        _code, out, _err = await _git_out(repo, *args)
        return out

    head = (await _git("rev-parse", "HEAD")).strip()
    # Include staged + unstaged so restore can rebuild pre-round dirtiness.
    diff = await _git("diff", "--binary", "HEAD")
    changed_raw = await _git("diff", "--name-only", "HEAD")
    changed = [line for line in changed_raw.splitlines() if line.strip()]
    untracked_raw = await _git("ls-files", "--others", "--exclude-standard")
    untracked = [line for line in untracked_raw.splitlines() if line.strip()]
    return {
        "head": head,
        "diff": diff,
        "changed": changed,
        "untracked": untracked,
    }


def _live_claimed_paths() -> set[str]:
    """Files another live session is actively working on — never revert these.

    Uses the body-coordination registry: a beacon's claim means a real session
    (or the owner, through one) has that file open right now.
    """
    out: set[str] = set()
    with suppress(Exception):
        from remedy.core.coordination import active_beacons

        for beacon in active_beacons():
            for claimed in beacon.live_claims():
                out.add(str(claimed).replace("\\", "/").lower())
    return out


async def git_restore(
    repo: str | Path,
    snapshot: dict[str, Any],
    *,
    round_paths: list[str] | None = None,
) -> str:
    """Restore the tree to the pre-round snapshot without nuking unrelated work.

    1. ``git reset --hard HEAD`` — discard tracked changes made during the round
       (index + worktree) back to the current HEAD.
    2. Re-apply the **captured** pre-round patch so sibling dirty work that was
       already present when the round started returns (not wiped forever).
    3. Delete untracked files **this round created** — and only those.

    ``round_paths`` is the round's own write set. Without it there is no way to
    tell round debris from a file the owner (or a concurrent session) just
    created, so nothing untracked is deleted at all. Leaving a stray artifact is
    always better than destroying someone's new work: this loop has silently
    eaten concurrent edits before.
    """
    import tempfile

    repo = Path(repo)
    errors: list[str] = []

    protected = _live_claimed_paths()

    def _held_by_live_session(rel: str) -> bool:
        with suppress(Exception):
            return str((repo / rel).resolve()).replace("\\", "/").lower() in protected
        return False

    # Scoped restore: when the round declares its write set AND the tree was
    # clean at snapshot time (the normal unattended case), revert exactly those
    # paths. A blanket `reset --hard` would also wipe tracked edits the owner
    # made while the round was drafting — which is how this loop ate real work.
    snap_was_clean = not (snapshot.get("changed") or snapshot.get("untracked"))
    scoped = bool(round_paths) and snap_was_clean
    if scoped:
        paths = [
            str(p).replace("\\", "/")
            for p in (round_paths or [])
            if str(p).strip() and not _held_by_live_session(str(p))
        ]
        if paths:
            code, _out, err = await _git_out(repo, "checkout", "HEAD", "--", *paths)
            # Paths that only ever existed untracked have no HEAD version —
            # not an error worth surfacing.
            if code != 0 and err.strip() and "did not match" not in err.lower():
                errors.append(err.strip())
    else:
        code, _out, err = await _git_out(repo, "reset", "--hard", "HEAD")
        if code != 0 and err.strip():
            errors.append(err.strip())

    diff = "" if scoped else str(snapshot.get("diff") or "")
    if diff.strip():
        # Apply via temp file so binary patches and large diffs work.
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=".patch",
                delete=False,
            ) as fh:
                fh.write(diff.encode("utf-8", "replace"))
                patch_path = fh.name
            try:
                code, _out, err = await _git_out(
                    repo,
                    "apply",
                    "--binary",
                    "--whitespace=nowarn",
                    patch_path,
                )
                if code != 0:
                    # Fallback: try 3-way for slightly drifted trees
                    code2, _o2, err2 = await _git_out(
                        repo,
                        "apply",
                        "--binary",
                        "--3way",
                        "--whitespace=nowarn",
                        patch_path,
                    )
                    if code2 != 0:
                        errors.append((err or err2 or "git apply failed").strip())
            finally:
                with suppress(Exception):
                    Path(patch_path).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"patch restore failed: {exc}")

    # Drop untracked files THIS ROUND created — nothing else.
    if round_paths:
        snap_untracked = {
            str(p).replace("\\", "/") for p in (snapshot.get("untracked") or [])
        }
        mine = {norm_rel(p) for p in round_paths if str(p).strip()}
        protected = _live_claimed_paths()
        _code, cur_raw, _err = await _git_out(
            repo, "ls-files", "--others", "--exclude-standard"
        )
        for rel in cur_raw.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            norm = rel.replace("\\", "/")
            if norm in snap_untracked:
                continue  # already dirty before the round started
            if norm not in mine:
                continue  # someone else's file — not ours to delete
            target = repo / rel
            with suppress(Exception):
                if str(target.resolve()).replace("\\", "/").lower() in protected:
                    continue  # a live session is working on it right now
            with suppress(Exception):
                if target.is_file():
                    target.unlink()
                elif target.is_dir():
                    import shutil

                    shutil.rmtree(target, ignore_errors=True)
    elif snapshot.get("untracked") is not None:
        logger.info(
            "self-inject restore: no round write set — leaving untracked files "
            "alone rather than risking concurrent work"
        )

    return "; ".join(e for e in errors if e)


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
    from remedy.execution.host.runner import prepare_host_command

    prepared = prepare_host_command(cmd, project_path=workdir)
    argv = prepared.argv
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
    for _t, cmd in zip(trees, round_.gate_cmds, strict=False):
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
                    _record_soul_lesson(round_, home)
                    return round_
            if round_.tree in ("python", "both"):
                requested = request_sidecar_restart(
                    home, repo=repo, snapshot=snapshot, round_id=round_.round_id
                )
                # Honest ledger: "applied" without a restart request means the
                # running serve still executes the old code (frozen install).
                round_.detail["sidecar_restart_requested"] = bool(requested)
        round_.outcome = "applied"
        round_.status = "applied"
    else:
        err = await git_restore(repo, snapshot)
        round_.outcome = "rolled_back"
        round_.status = "rolled_back"
        round_.detail["rollback_error"] = err or ""
    round_.finished_utc = _now_utc()
    _record_soul_lesson(round_, home)
    return round_


def _record_soul_lesson(round_: SelfInjectRound, home: str | Path | None) -> None:
    """Fold self-inject outcomes into the organism soul (personhood self-model)."""
    with suppress(Exception):
        from remedy.memory.soul.update import record_self_inject_lesson

        gate_blob = ""
        go = (round_.detail or {}).get("gate_output") or {}
        if isinstance(go, dict) and go:
            # Last gate output snippet
            gate_blob = str(next(iter(go.values()), ""))[:400]
        record_self_inject_lesson(
            outcome=round_.outcome or round_.status,
            tree=round_.tree,
            summary=round_.summary or "",
            round_id=round_.round_id,
            gate_detail=gate_blob,
            home=home,
        )


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
    payload: dict[str, Any] = {
        "ts": _now_utc(),
        "kind": "sidecar_restart",
        "round_id": round_id,
        "repo": str(repo) if repo else "",
        "head": (snapshot or {}).get("head", ""),
        "changed": (snapshot or {}).get("changed", []),
        "untracked": (snapshot or {}).get("untracked", []),
    }
    from remedy.core.runtime_identity import runs_this_checkout

    if not runs_this_checkout():
        # Frozen Desktop is not this checkout. Recycling serve mid-turn just
        # drops the SSE ("Error: network error") and cannot load repo edits.
        # Frozen only — the dev-checkout Desktop also sets the sidecar env
        # vars (_is_packaged_runtime), but its serve DOES run this checkout,
        # so it must still request the restart to load the edit.
        logger.info(
            "skip sidecar restart request — frozen install (round=%s)",
            round_id or "-",
        )
        return False
    try:
        locks.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            locks / "self_inject_apply", payload, indent=None, ensure_ascii=False
        )
        return True
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Idle trigger + unattended improve
# ---------------------------------------------------------------------------

# Last *user* turn (chat/messenger), not health-check log mtime.
# 0 = treat process start as the last activity (do not fire on boot).
_last_user_activity: float = 0.0
_process_started: float = time.time()
_last_unattended_code: float = 0.0
_CODE_DRAFT_COOLDOWN_S = 900.0


def note_user_activity() -> None:
    """Mark a real owner turn so idle self-improve waits for quiet."""
    with suppress(Exception):
        from remedy.core.self_inject_draft import in_internal_improve

        if in_internal_improve():
            return
    global _last_user_activity
    _last_user_activity = time.time()


def last_tick_path(home: str | Path | None = None) -> Path:
    return _home_dir(home) / "self_improve_last.json"


def read_last_tick(home: str | Path | None = None) -> dict[str, Any] | None:
    """Most recent unattended tick, or None if none has run."""
    path = last_tick_path(home)
    if not path.is_file():
        return None
    with suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return None


def activity_snapshot(home: str | Path | None = None) -> dict[str, Any]:
    """Idle-clock + last-tick view for status tools / API."""
    update: dict[str, Any] = {}
    pending = None
    with suppress(Exception):
        from remedy.core.self_inject_draft import (
            client_update_policy,
            read_pending_ship,
        )

        update = client_update_policy(_guess_repo(None))
        pending = read_pending_ship(home)
    return {
        "enabled": is_enabled(home),
        "idle_ready": should_run_now(home),
        "idle_s": round(_idle_seconds(), 1),
        "idle_threshold_s": _idle_threshold(home),
        "last_user_activity": _last_user_activity,
        "process_started": _process_started,
        "last_tick": read_last_tick(home),
        "update": update,
        "pending_ship": pending,
    }


def _is_packaged_runtime() -> bool:
    """Desktop sidecar / frozen install — do not mutate the source tree by default."""
    from remedy.core.runtime_identity import is_desktop_runtime

    return is_desktop_runtime()


def is_enabled(home: str | Path | None = None) -> bool:
    """True when self-inject auto-triggering is enabled (env or config)."""
    _ = home
    if os.environ.get("REMEDY_SELF_INJECT") == "0":
        return False
    if os.environ.get("REMEDY_SELF_INJECT") == "1":
        return True
    packaged = _is_packaged_runtime()
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        cfg = load_config()
        si = cfg.get("self_inject")
        if isinstance(si, dict) and "enabled" in si:
            return bool(si["enabled"])
    return not packaged


def should_run_now(home: str | Path | None = None) -> bool:
    """True if a round should run now: enabled AND (force flag or idle window).

    ``REMEDY_SELF_INJECT_FORCE`` bypasses idle detection. Otherwise the product
    must be idle of **user turns** for ``idle_seconds`` (default 300). Status
    pings / debug.log writes do not count as activity.
    """
    if os.environ.get("REMEDY_SELF_INJECT_FORCE") == "1":
        return True
    if not is_enabled(home):
        return False
    idle_s = _idle_seconds()
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


def _idle_seconds() -> float:
    """Seconds since the last user turn (or process start if none yet)."""
    mark = _last_user_activity if _last_user_activity > 0 else _process_started
    return max(0.0, time.time() - mark)


def _record_tick(home: str | Path | None, payload: dict[str, Any]) -> None:
    org = payload.get("organism") if isinstance(payload, dict) else None
    code = payload.get("code") if isinstance(payload, dict) else None
    quiet = isinstance(org, dict) and not (
        org.get("life_step")
        or org.get("recalled")
        or org.get("dreamed")
        or org.get("skills_refined")
        or org.get("cas_compact")
    )
    skipped = isinstance(code, dict) and bool(code.get("skipped"))
    if quiet and (code is None or skipped):
        prev = read_last_tick(home)
        if prev:
            return
    path = last_tick_path(home)
    with suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = dict(payload)
        blob["ts"] = _now_utc()
        write_json_atomic(path, blob, ensure_ascii=False)


async def run_unattended_improve(
    runtime: Any = None,
    *,
    home: str | Path | None = None,
    repo: str | Path | None = None,
) -> dict[str, Any]:
    """One unattended improve tick: learn/lifecycle + optional code self-heal.

    Organism work (skill promote/prune, soul dream, persist) always runs.
    Code drafts wait for the user-idle window (or ``REMEDY_SELF_INJECT_FORCE``),
    a clean tree, and a cooldown. Does **not** run the full pytest suite.
    """
    home = home or getattr(runtime, "home_dir", None) or getattr(
        getattr(runtime, "config", None), "home_dir", None
    )
    result: dict[str, Any] = {
        "kind": "unattended",
        "organism": {},
        "code": None,
        "idle_s": round(_idle_seconds(), 1),
        "idle_ready": should_run_now(home),
    }
    result["organism"] = _organism_tick(runtime, home=home)

    if repo is None:
        repo = _guess_repo(runtime)
    repo_p = Path(repo) if repo else None
    global _last_unattended_code
    now = time.time()
    code_due = should_run_now(home) and (
        (now - _last_unattended_code) >= _CODE_DRAFT_COOLDOWN_S
    )
    if repo_p is not None and (repo_p / "pyproject.toml").is_file() and code_due:
        # Count the attempt so a clean/no-op tree does not retry every tick.
        _last_unattended_code = now
        drafted = None
        with suppress(Exception):
            from remedy.core.self_inject_draft import run_unattended_draft

            drafted = await run_unattended_draft(runtime, repo=repo_p, home=home)
        if drafted and not drafted.get("skipped"):
            result["code"] = drafted
        elif drafted and drafted.get("skipped") in (
            "not_source_checkout",
            "user_streaming",
            "red_cooldown",
            "dirty_tree",
        ):
            result["code"] = drafted
        else:
            code = await _maybe_ruff_self_heal(repo_p, home=home)
            if code is not None:
                result["code"] = code
            elif drafted:
                result["code"] = drafted
            else:
                result["code"] = {"skipped": "clean_or_dirty"}
    elif not should_run_now(home):
        result["code"] = {"skipped": "not_idle"}
    _record_tick(home, result)
    return result


_last_skill_tick = 0.0
_SKILL_TICK_TTL = 600.0


def _organism_tick(runtime: Any, *, home: str | Path | None) -> dict[str, Any]:
    """Cheap unattended learning: promote/prune skills, dream, persist organs."""
    out: dict[str, Any] = {"skills_refined": 0, "dreamed": False}
    global _last_skill_tick
    now_sk = time.time()
    if now_sk - _last_skill_tick >= _SKILL_TICK_TTL:
        with suppress(Exception):
            loop = getattr(runtime, "_get_learning_loop", None)
            ll = loop() if callable(loop) else getattr(runtime, "learning_loop", None)
            if ll is not None and hasattr(ll, "tick_learned_skills"):
                changed = ll.tick_learned_skills() or []
                out["skills_refined"] = len(changed)
                out["skill_changes"] = changed[:12]
                _last_skill_tick = now_sk
    with suppress(Exception):
        from remedy.core.metabolism.cua_macros import get_cua_macros
        from remedy.core.metabolism.skill_genome import get_skill_genome

        get_cua_macros().persist(home)
        get_skill_genome().persist(home)
    with suppress(Exception):
        from remedy.memory.soul.dream import dream_cycle, should_dream
        from remedy.memory.soul.field import load_soul_field

        if should_dream(home):
            sf = load_soul_field(home)
            if len(sf.episodes) >= 4:
                dream_cycle(
                    home=home,
                    memory=getattr(runtime, "memory", None) if runtime else None,
                    field=sf,
                )
                out["dreamed"] = True
    with suppress(Exception):
        from remedy.core.metabolism.organism import organism_cycle

        cycle = organism_cycle(home, runtime=runtime, session_id="life")
        out.update(cycle)
    return out


def _guess_repo(runtime: Any) -> Path | None:
    with suppress(Exception):
        import remedy as _pkg

        cand = Path(_pkg.__file__).resolve().parent.parent.parent
        for _ in range(6):
            if (cand / "pyproject.toml").is_file():
                return cand
            cand = cand.parent
    with suppress(Exception):
        raw = runtime.effective_project_path() if runtime is not None else None
        if raw:
            p = Path(str(raw))
            if (p / "pyproject.toml").is_file():
                return p
    return None


async def _maybe_ruff_self_heal(
    repo: Path, *, home: str | Path | None
) -> dict[str, Any] | None:
    """If the Remedy tree is clean, apply ``ruff --fix`` and keep it only if still clean."""
    with suppress(Exception):
        from remedy.core.self_inject_draft import is_source_checkout

        if not is_source_checkout(repo):
            return None
    snap = await git_capture(repo)
    if snap.get("changed") or snap.get("untracked"):
        return None
    src = repo / "src" / "remedy"
    if not src.is_dir():
        return None
    fix_cmd = "uv run ruff check --fix src/remedy"
    gate_cmd = "uv run ruff check src/remedy"
    code_fix, out_fix, err_fix = await _run_one(fix_cmd, repo, 120.0)
    after = await git_capture(repo)
    if not after.get("changed"):
        return None
    round_ = SelfInjectRound(
        tree="python",
        summary="unattended ruff --fix self-heal",
    )
    round_.gate_cmds = [gate_cmd]
    code_gate, out_g, err_g = await _run_one(gate_cmd, repo, 120.0)
    round_.gate_exit_codes[gate_cmd] = code_gate
    round_.detail["gate_output"] = {gate_cmd: (out_g + err_g)[-1500:]}
    round_.detail["ruff_fix_exit"] = code_fix
    round_.detail["ruff_fix_tail"] = (out_fix + err_fix)[-400:]
    round_.status = "green" if code_gate == 0 else "red"
    round_ = await apply_or_rollback(round_, repo, snap, home=home)
    append_ledger(round_, home)
    return {
        "round_id": round_.round_id,
        "status": round_.status,
        "outcome": round_.outcome,
        "changed": after.get("changed") or [],
    }

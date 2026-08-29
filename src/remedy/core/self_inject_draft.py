"""Bounded unattended draft: one evidenced target, one LLM attempt, gate, stop.

This is the only path that may *write* Remedy source without a user prompt.
It never ships. Clients consume GitHub/PyPI releases; they do not merge
local self-improve with an official update.

See ``docs/SELF_INJECT.md`` § Client updates.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.self_inject import (
    SelfInjectRound,
    _now_utc,
    _run_one,
    append_ledger,
    apply_or_rollback,
    git_capture,
    git_restore,
    read_ledger,
)

_INTERNAL: ContextVar[bool] = ContextVar("remedy_internal_improve", default=False)
_RED_COOLDOWN_S = 6 * 3600.0
_MAX_CHANGED_FILES = 3
_MAX_DIFF_LINES = 220
_DRAFT_STEPS = 8
_DRAFT_TIMEOUT_S = 180.0
_RUFF_DRAFT_CODES = frozenset(
    {"F821", "F822", "F811", "F823", "E9", "F63", "F7", "F82"}
)
INTERNAL_IMPROVE_TOOLS = frozenset(
    {
        "file_read",
        "file_edit",
        "file_edit_batch",
        "repo_search",
        "list_dir",
        "bash_exec",
    }
)
_SHELL_OK = re.compile(
    r"^(uv\s+run\s+)?(ruff|pytest)\b"
    r"|^(python|pythonw|py)\s+-m\s+(pytest|py_compile|ruff)\b",
    re.IGNORECASE,
)
_SHELL_BAN = re.compile(
    r"\b(git\s+push|gh\s+|npm\s+publish|twine|uv\s+publish|"
    r"pip\s+install|rm\s+-rf|del\s+/|format\s+)\b",
    re.IGNORECASE,
)
_SHELL_CHAIN = re.compile(r"[;&|`\n\r]|\$\(|&&|\|\||>")


def in_internal_improve() -> bool:
    return bool(_INTERNAL.get())


# Exact attribution: the paths THIS round's own tools wrote. Without it we can
# only diff the tree, which cannot tell a rogue draft from the owner editing in
# another window — and guessing wrong destroys real work.
_ROUND_WRITES: ContextVar[set[str] | None] = ContextVar(
    "remedy_internal_improve_writes", default=None
)


@contextmanager
def internal_improve_context() -> Iterator[set[str]]:
    """Mark this coroutine as an unattended self-fix (not a user turn).

    Yields the live set of paths the round writes, so the caller can keep the
    reference after the context resets the ContextVar.
    """
    token = _INTERNAL.set(True)
    writes: set[str] = set()
    writes_token = _ROUND_WRITES.set(writes)
    try:
        yield writes
    finally:
        _INTERNAL.reset(token)
        _ROUND_WRITES.reset(writes_token)


def note_internal_write(path: str | Path) -> None:
    """Called by the file tools when an unattended draft writes something."""
    bucket = _ROUND_WRITES.get()
    if bucket is None:
        return
    with suppress(Exception):
        bucket.add(str(path).replace("\\", "/"))


def round_writes() -> set[str]:
    """Paths this round actually wrote (empty outside a draft)."""
    return set(_ROUND_WRITES.get() or set())


def internal_improve_shell_ok(command: str) -> bool:
    """True when bash_exec is allowed during an unattended draft.

    No chaining, no ``python -c``, no git/gh. Prefix-only matching is not enough.
    """
    cmd = (command or "").strip()
    if not cmd:
        return False
    if _SHELL_CHAIN.search(cmd):
        return False
    if _SHELL_BAN.search(cmd):
        return False
    return bool(_SHELL_OK.match(cmd))


def is_source_checkout(repo: str | Path | None) -> bool:
    """True only for a git worktree of this product (not a packaged install)."""
    if repo is None:
        return False
    root = Path(repo)
    git = root / ".git"
    if not git.exists():
        return False
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return False
    try:
        text = pp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(re.search(r'(?m)^name\s*=\s*["\']remedy-ai["\']', text))


def client_update_policy(repo: str | Path | None = None) -> dict[str, Any]:
    """How this install takes official updates vs local self-improve.

    Packaged / pip clients **replace** on update and never self-edit code.
    A source checkout may self-draft locally; those edits reach other
    clients only after a gated ship. If a source tree is dirty when an
    official update arrives, origin wins — we do not merge LLM patches.
    """
    source = is_source_checkout(repo)
    return {
        "mode": "source_ship" if source else "replace",
        "self_improve_code": source,
        "on_conflict": "origin_wins",
        "ship_from_idle": False,
        "note": (
            "Source checkout may draft locally; ship via git_push/gh_release. "
            "Packaged clients never rewrite their install — the signed "
            "release replaces them. Local self-improve is never merged into "
            "an official update."
        ),
    }


def pending_ship_path(home: str | Path | None = None) -> Path:
    from remedy.core.self_inject import _home_dir

    return _home_dir(home) / "self_improve_pending_ship.json"


def read_pending_ship(home: str | Path | None = None) -> dict[str, Any] | None:
    path = pending_ship_path(home)
    if not path.is_file():
        return None
    with suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return None


def _fault_report(target: DraftTarget, round_: SelfInjectRound, changed: list[str]) -> str:
    """What broke, and the fix that proved itself — ready to send upstream."""
    gates = "\n".join(
        f"- `{cmd}` → exit {code}"
        for cmd, code in (round_.gate_exit_codes or {}).items()
    )
    files = "\n".join(f"- `{c}`" for c in changed[:8]) or "- (none)"
    return (
        "### What went wrong\n"
        f"{target.evidence}\n\n"
        "### Where\n"
        f"`{target.path}`\n\n"
        "### Context\n"
        f"{target.why}\n\n"
        "### Fix\n"
        f"{round_.summary}\n\n"
        "**Files changed**\n"
        f"{files}\n\n"
        "**Verified by**\n"
        f"{gates or '- (no gate recorded)'}\n\n"
        "_Found and fixed automatically on a source checkout; the patch passed "
        "its gate locally before this report was queued. Not applied to any "
        "other install._"
    )


def write_pending_ship(
    home: str | Path | None,
    *,
    round_id: str,
    summary: str,
    changed: list[str],
    report: str = "",
) -> Path:
    """Queue a local-green draft for a *human/Auto ship*. Never pushes."""
    path = pending_ship_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "round_id": round_id,
        "summary": summary[:240],
        "changed": list(changed)[:12],
        "report": str(report or "")[:4000],
        "ts": _now_utc(),
        "ship": False,
        "note": (
            "Local only. Submit with self_improve_submit_issue (owner Approve) "
            "as an inbox comment — no branch, no release."
        ),
    }
    write_json_atomic(path, payload, ensure_ascii=False)
    return path


def draft_state_path(home: str | Path | None = None) -> Path:
    from remedy.core.self_inject import _home_dir

    return _home_dir(home) / "self_improve_draft.json"


def _load_draft_state(home: str | Path | None) -> dict[str, Any]:
    path = draft_state_path(home)
    if not path.is_file():
        return {}
    with suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def _save_draft_state(home: str | Path | None, data: dict[str, Any]) -> None:
    path = draft_state_path(home)
    with suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, data, ensure_ascii=False)


def red_blocked(home: str | Path | None, key: str) -> bool:
    st = _load_draft_state(home)
    if st.get("last_red_key") != key:
        return False
    try:
        age = time.time() - float(st.get("last_red_ts") or 0)
    except (TypeError, ValueError):
        return False
    return age < _RED_COOLDOWN_S


def record_red(home: str | Path | None, key: str) -> None:
    st = _load_draft_state(home)
    st["last_red_key"] = key
    st["last_red_ts"] = time.time()
    st["last_outcome"] = "red"
    _save_draft_state(home, st)


def record_green(home: str | Path | None, key: str) -> None:
    st = _load_draft_state(home)
    st["last_green_key"] = key
    st["last_green_ts"] = time.time()
    st["last_outcome"] = "green"
    if st.get("last_red_key") == key:
        st.pop("last_red_key", None)
        st.pop("last_red_ts", None)
    _save_draft_state(home, st)


@dataclass
class DraftTarget:
    kind: str
    path: str
    test_id: str = ""
    evidence: str = ""
    allowed: list[str] = field(default_factory=list)
    why: str = ""

    def key(self) -> str:
        return f"{self.kind}:{self.path}:{self.test_id}"

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def infer_source_from_test(repo: Path, test_id: str) -> str | None:
    """Map ``tests/test_foo.py::test_bar`` → ``src/remedy/**/foo.py`` if unique."""
    node = (test_id or "").split("::", 1)[0].replace("\\", "/")
    stem = Path(node).stem
    if stem.startswith("test_"):
        name = stem[5:]
    elif stem.endswith("_test"):
        name = stem[:-5]
    else:
        return None
    if not name or not name.replace("_", "").isalnum():
        return None
    src = repo / "src" / "remedy"
    if not src.is_dir():
        return None
    matches = list(src.rglob(f"{name}.py"))
    if not matches:
        return None
    rels = [m.relative_to(repo).as_posix() for m in matches]
    for rel in rels:
        if "/core/" in f"/{rel}":
            return rel
    return rels[0]


def _rel_under_src(repo: Path, raw: str) -> str | None:
    try:
        p = Path(raw)
        p = (repo / p).resolve() if not p.is_absolute() else p.resolve()
        rel = p.relative_to(repo.resolve()).as_posix()
    except Exception:
        return None
    if not rel.startswith("src/remedy/") or not rel.endswith(".py"):
        return None
    if ".." in rel:
        return None
    return rel


def pick_draft_target(
    repo: str | Path,
    home: str | Path | None = None,
) -> DraftTarget | None:
    """Pick one evidenced defect. No evidence → None (do not invent work).

    Faults Remedy actually hit come first and, by default, are the ONLY trigger:
    self-improvement is for fixing what broke during real work, not for going
    looking for something to change. The speculative pickers (pytest's stale
    lastfailed cache, ruff nits) are opt-in via ``REMEDY_SELF_INJECT_SPECULATIVE=1``
    — the first one ever run picked a network flake, which no edit can fix.
    """
    repo_p = Path(repo)
    if not is_source_checkout(repo_p):
        return None
    pickers: list[Any] = [lambda: _from_fault(repo_p, home)]
    if os.environ.get("REMEDY_SELF_INJECT_SPECULATIVE") == "1":
        pickers += [
            lambda: _from_lastfailed(repo_p),
            lambda: _from_traceback(repo_p, home),
            lambda: _from_ledger_red(repo_p, home),
            lambda: _from_ruff(repo_p),
        ]
    for picker in pickers:
        with suppress(Exception):
            tgt = picker()
            if tgt is not None:
                return tgt
    return None


def _from_fault(repo: Path, home: str | Path | None) -> DraftTarget | None:
    """A fault Remedy actually hit during real work — the primary trigger.

    Environmental faults (network, provider auth, missing toolchain) are
    recorded but never targeted: no edit to this repo can fix the world.
    """
    from remedy.core.error_journal import next_target_fault

    fault = next_target_fault(home=home)
    if fault is None:
        return None
    # Point the jail at the file the traceback blames, when we can find it.
    allowed: list[str] = []
    src_hint = ""
    with suppress(Exception):
        name = (fault.where or "").split(":")[0].strip()
        if name.endswith(".py"):
            matches = [
                p.relative_to(repo).as_posix()
                for p in (repo / "src").rglob(name)
                if p.is_file()
            ]
            if len(matches) == 1:
                src_hint = matches[0]
                allowed = [src_hint]
    if not allowed:
        allowed = ["src/remedy/"]
    evidence = (
        f"{fault.exc_type or 'error'} at {fault.where or '?'} "
        f"(hit {fault.count}x): {fault.message}"
    )
    return DraftTarget(
        kind="fault",
        path=src_hint or (fault.where or "src/remedy/"),
        test_id="",
        evidence=evidence[:400],
        allowed=allowed,
        why=(
            f"Remedy hit this while working: {fault.message[:160]}. "
            f"Context: {fault.context[:120] or 'n/a'}. Fault id {fault.id}."
        ),
    )


def _from_lastfailed(repo: Path) -> DraftTarget | None:
    cache = repo / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not cache.is_file():
        return None
    data = json.loads(cache.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    for nodeid, failed in data.items():
        if not failed:
            continue
        nid = str(nodeid)
        src = infer_source_from_test(repo, nid)
        if not src:
            continue
        test_file = nid.split("::", 1)[0].replace("\\", "/")
        allowed = [src]
        if (repo / test_file).is_file():
            allowed.append(test_file)
        return DraftTarget(
            kind="pytest_lastfailed",
            path=src,
            test_id=nid,
            evidence=f"pytest lastfailed: {nid}",
            allowed=allowed,
            why="Last recorded test failure on this checkout",
        )
    return None


_TB_FILE = re.compile(r'File "([^"]+)", line (\d+)')


def _from_traceback(repo: Path, home: str | Path | None) -> DraftTarget | None:
    from remedy.core.self_inject import _home_dir

    candidates = [
        _home_dir(home) / "debug.log",
        _home_dir(home) / "logs" / "errors.log",
        _home_dir(home) / "errors.log",
    ]
    blob = ""
    for p in candidates:
        if p.is_file():
            with suppress(Exception):
                blob = p.read_text(encoding="utf-8", errors="replace")[-80_000:]
                if "Traceback" in blob or "Error" in blob:
                    break
    if not blob:
        return None
    hits = _TB_FILE.findall(blob)
    if not hits:
        return None
    # Last frame inside src/remedy
    for raw, line_no in reversed(hits):
        rel = _rel_under_src(repo, raw)
        if rel is None:
            continue
        return DraftTarget(
            kind="traceback",
            path=rel,
            evidence=f"{rel}:{line_no} from recent traceback",
            allowed=[rel],
            why="Recent process traceback in Remedy source",
        )
    return None


def _from_ledger_red(repo: Path, home: str | Path | None) -> DraftTarget | None:
    rows = read_ledger(home)
    for row in reversed(rows[-8:]):
        if str(row.get("status") or "") not in ("red", "rolled_back"):
            continue
        go = (row.get("detail") or {}).get("gate_output") or {}
        text = ""
        if isinstance(go, dict):
            text = "\n".join(str(v) for v in go.values())
        m = re.search(r"(src/remedy/[A-Za-z0-9_./\\-]+\.py)", text)
        if not m:
            continue
        rel = _rel_under_src(repo, m.group(1))
        if not rel:
            continue
        return DraftTarget(
            kind="ledger_red",
            path=rel,
            evidence=text[-800:],
            allowed=[rel],
            why=f"Prior self-inject red {row.get('round_id', '')}",
        )
    return None


def _from_ruff(repo: Path) -> DraftTarget | None:
    src = repo / "src" / "remedy"
    if not src.is_dir():
        return None
    try:
        from remedy.core.build_python import python_cmd_for_subprocess
        from remedy.execution.process import hidden_subprocess_kwargs

        py = python_cmd_for_subprocess(repo)
        if not py:
            return None
        proc = subprocess.run(
            [*py, "-m", "ruff", "check", "--output-format=json", "src/remedy"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = (proc.stdout or "").strip()
    if not raw.startswith("["):
        return None
    with suppress(Exception):
        items = json.loads(raw)
        if not isinstance(items, list):
            return None
        for it in items:
            if not isinstance(it, dict):
                continue
            code = str(it.get("code") or "")
            if code not in _RUFF_DRAFT_CODES and not any(
                code.startswith(p) for p in ("E9", "F8")
            ):
                continue
            fname = str(it.get("filename") or it.get("file") or "")
            rel = _rel_under_src(repo, fname)
            if not rel:
                continue
            msg = str(it.get("message") or code)
            loc = it.get("location") or {}
            line = loc.get("row") or it.get("location", {}).get("row") or "?"
            return DraftTarget(
                kind="ruff",
                path=rel,
                evidence=f"{rel}:{line} {code} {msg}",
                allowed=[rel],
                why=f"Ruff {code} needs a real edit (not --fix)",
            )
    return None


def _jail_violation(changed: list[str], allowed: list[str]) -> str | None:
    from remedy.core.self_inject_guard import normalize_rel

    allow = {normalize_rel(a) or "" for a in allowed}
    allow.discard("")
    extra: list[str] = []
    for c in changed:
        n = normalize_rel(c)
        if not n or n not in allow:
            extra.append(c.replace("\\", "/"))
    if extra:
        return "outside_allowed:" + ",".join(extra[:8])
    if len(changed) > _MAX_CHANGED_FILES:
        return f"too_many_files:{len(changed)}"
    return None


def _diff_too_large(diff: str) -> bool:
    lines = [
        ln
        for ln in (diff or "").splitlines()
        if ln.startswith("+") or ln.startswith("-")
    ]
    # ignore +++ / --- headers
    lines = [ln for ln in lines if not ln.startswith("+++") and not ln.startswith("---")]
    return len(lines) > _MAX_DIFF_LINES


def _has_llm(runtime: Any) -> bool:
    if runtime is None:
        return False
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding

        bind = get_llm_binding(runtime)
        return bool(getattr(bind, "api_key", None))
    return bool(getattr(runtime, "_llm_api_key", None))


def _user_streaming(runtime: Any) -> bool:
    """Skip drafts while any session is streaming (including self-improve)."""
    try:
        from remedy.core.turn_context import any_stream_claimed

        if any_stream_claimed():
            return True
    except Exception:
        pass
    sess = getattr(runtime, "_streaming_sessions", None)
    return bool(sess)


def _draft_prompt(target: DraftTarget) -> str:
    allowed = ", ".join(target.allowed) or target.path
    return (
        "UNATTENDED SELF-FIX. No user is watching. You are Remedy fixing "
        "YOUR OWN repo.\n\n"
        f"Kind: {target.kind}\n"
        f"File: {target.path}\n"
        f"Test: {target.test_id or '(none)'}\n"
        f"Why: {target.why}\n"
        f"Evidence:\n{target.evidence[:1200]}\n\n"
        "Rules:\n"
        f"- Only edit these paths: {allowed}\n"
        "- Smallest correct fix. Prefer one file.\n"
        "- Read the file first. If the evidence does not prove a bug, do nothing.\n"
        "- Do not git commit, push, release, or touch secrets/config/CI.\n"
        "- bash_exec only for pytest / ruff / py_compile on this repo.\n"
        "- Stop after the edit. Do not start a new feature.\n"
    )


async def _drain_internal_turn(
    runtime: Any, prompt: str, repo: Path, *, timeout: float
) -> str:
    chunks: list[str] = []
    total = 0

    async def _run() -> None:
        nonlocal total
        async for tok in runtime.stream_response(
            prompt,
            session_id=None,
            internal=True,
            project_override=str(repo),
        ):
            if not isinstance(tok, str):
                continue
            if tok.startswith("@@"):
                continue
            chunks.append(tok)
            total += len(tok)
            if total > 24_000:
                break

    import asyncio

    await asyncio.wait_for(_run(), timeout=timeout)
    return "".join(chunks)[:4000]


def _q(path: str) -> str:
    """Quote only when needed.

    prepare_host_command splits on whitespace and does NOT strip quotes, so an
    always-quoted path reaches the tool as a literal `"tests/x.py"` — which is
    why every gate failed on Windows with ruff E902 "filename syntax is
    incorrect". Unquoted is correct for the space-free repo paths we generate.
    """
    s = str(path or "")
    return f'"{s}"' if (" " in s or "\t" in s) else s


def _gate_cmds(repo: Path, target: DraftTarget, changed: list[str]) -> list[str]:
    cmds: list[str] = []
    py = [c for c in changed if c.endswith(".py")]
    if py:
        joined = " ".join(_q(c) for c in py[:6])
        cmds.append(f"uv run ruff check {joined}")
    if target.test_id:
        cmds.append(f"uv run pytest -q {_q(target.test_id)}")
    else:
        with suppress(Exception):
            from remedy.core.build_scoped import map_source_to_test_candidates

            tests: list[str] = []
            for c in changed:
                for tp in map_source_to_test_candidates(c.replace("\\", "/"), repo):
                    try:
                        tests.append(tp.relative_to(repo).as_posix())
                    except Exception:
                        tests.append(str(tp))
            if tests:
                cmds.append(f"uv run pytest -q {_q(tests[0])}")
    if not cmds and py:
        cmds.append(f"uv run python -m py_compile {_q(py[0])}")
    return cmds


async def run_unattended_draft(
    runtime: Any,
    *,
    repo: str | Path,
    home: str | Path | None = None,
    target: DraftTarget | None = None,
) -> dict[str, Any]:
    """One attempt. Dirty tree / packaged install / no evidence → skip.

    Never git-pushes. Green drafts write ``self_improve_pending_ship.json``.
    """
    repo_p = Path(repo)
    policy = client_update_policy(repo_p)
    if not policy["self_improve_code"]:
        return {"skipped": "not_source_checkout", "update": policy}
    snap = await git_capture(repo_p)
    if snap.get("changed") or snap.get("untracked"):
        return {"skipped": "dirty_tree", "update": policy}
    if _user_streaming(runtime):
        return {"skipped": "user_streaming"}
    tgt = target or pick_draft_target(repo_p, home)
    if tgt is None:
        return {"skipped": "no_evidence"}
    if red_blocked(home, tgt.key()):
        return {"skipped": "red_cooldown", "target": tgt.to_public()}
    if not _has_llm(runtime):
        return {"skipped": "no_llm", "target": tgt.to_public()}

    prev_steps = getattr(runtime, "_max_react_steps", None)
    assistant = ""
    own_writes: set[str] = set()
    try:
        if runtime is not None:
            runtime._max_react_steps = _DRAFT_STEPS
        with internal_improve_context() as _writes:
            assistant = await _drain_internal_turn(
                runtime, _draft_prompt(tgt), repo_p, timeout=_DRAFT_TIMEOUT_S
            )
        own_writes = set(_writes)
    except Exception as exc:  # noqa: BLE001
        with suppress(Exception):
            await git_restore(repo_p, snap)
        return {
            "skipped": "draft_error",
            "error": str(exc)[:300],
            "target": tgt.to_public(),
        }
    finally:
        if runtime is not None and prev_steps is not None:
            runtime._max_react_steps = prev_steps

    after = await git_capture(repo_p)
    changed = list(after.get("changed") or [])
    if not changed:
        return {"skipped": "no_edit", "target": tgt.to_public(), "assistant": assistant[:400]}

    # Attribution guard. The round starts on a clean tree, but the draft turn
    # takes minutes — anything the OWNER (or a concurrent session) edits in that
    # window also shows up in `changed`. Rolling that back destroyed real work
    # three times in one session. Files outside the round's own jail are, by
    # definition, not its business: never revert them, and stop the round.
    # Prefer EXACT attribution: what the round's own tools wrote. Only when that
    # is unavailable do we fall back to "inside the jail" as a proxy.
    mine = {c for c in changed if c in own_writes} if own_writes else set()
    if own_writes:
        foreign = [c for c in changed if c not in mine]
        if foreign:
            with suppress(Exception):
                await git_restore(repo_p, snap, round_paths=sorted(mine))
            record_red(home, tgt.key())
            return {
                "skipped": "concurrent_edit",
                "target": tgt.to_public(),
                "foreign": foreign[:8],
                "note": (
                    "Files changed that this round did not write — the owner or "
                    "another session was working. Left untouched."
                ),
            }
        changed = sorted(mine)

    allowed_now = [c for c in changed if not _jail_violation([c], tgt.allowed)]
    foreign = [c for c in changed if c not in allowed_now]
    if foreign and not own_writes:
        with suppress(Exception):
            await git_restore(repo_p, snap, round_paths=allowed_now)
        record_red(home, tgt.key())
        return {
            "skipped": "concurrent_edit",
            "target": tgt.to_public(),
            "foreign": foreign[:8],
            "note": (
                "Files changed outside this round's jail while it was drafting — "
                "treating them as someone else's work and leaving them untouched."
            ),
        }

    jail = _jail_violation(changed, tgt.allowed)
    if jail or _diff_too_large(str(after.get("diff") or "")):
        await git_restore(repo_p, snap, round_paths=changed)
        record_red(home, tgt.key())
        reason = jail or "diff_too_large"
        round_ = SelfInjectRound(
            tree="python",
            summary=f"unattended draft jail/size ({reason})",
            status="red",
        )
        round_.detail["target"] = tgt.to_public()
        round_.detail["jail"] = reason
        round_.outcome = "rolled_back"
        round_.status = "rolled_back"
        round_.finished_utc = _now_utc()
        append_ledger(round_, home)
        return {
            "outcome": "rolled_back",
            "reason": reason,
            "changed": changed,
            "target": tgt.to_public(),
            "round_id": round_.round_id,
        }

    cmds = _gate_cmds(repo_p, tgt, changed)
    round_ = SelfInjectRound(
        tree="python",
        summary=f"unattended draft {tgt.kind} {tgt.path}",
    )
    round_.detail["target"] = tgt.to_public()
    round_.gate_cmds = cmds
    all_green = True
    for cmd in cmds:
        code, out, err = await _run_one(cmd, repo_p, 180.0)
        round_.gate_exit_codes[cmd] = code
        round_.detail.setdefault("gate_output", {})[cmd] = (out + err)[-1500:]
        all_green = all_green and code == 0
    round_.status = "green" if all_green else "red"
    round_ = await apply_or_rollback(round_, repo_p, snap, home=home)
    append_ledger(round_, home)
    # A fault-driven round updates the journal either way: a failed attempt is
    # counted (so a stubborn fault stops burning rounds), a green one closes it.
    fault_id = ""
    if tgt.kind == "fault":
        with suppress(Exception):
            fault_id = str(tgt.why).split("Fault id ", 1)[-1].strip(" .")
    if round_.status in ("red", "rolled_back") or not all_green:
        record_red(home, tgt.key())
        if fault_id:
            with suppress(Exception):
                from remedy.core.error_journal import note_fix_attempt

                note_fix_attempt(fault_id, home=home)
    else:
        record_green(home, tgt.key())
        if fault_id:
            with suppress(Exception):
                from remedy.core.error_journal import mark_fixed

                mark_fixed(fault_id, home=home)
        # Queue the report + the verified fix for the owner to submit upstream.
        # Never posted automatically — self_improve_submit_issue needs Approve.
        write_pending_ship(
            home,
            round_id=round_.round_id,
            summary=round_.summary,
            changed=changed,
            report=_fault_report(tgt, round_, changed) if fault_id else "",
        )
    return {
        "round_id": round_.round_id,
        "status": round_.status,
        "outcome": round_.outcome,
        "changed": changed,
        "target": tgt.to_public(),
        "ship": False,
        "pending_ship": round_.outcome == "applied",
    }


def origin_wins_if_dirty(repo: str | Path) -> dict[str, Any]:
    """Updater helper: official origin/release replaces local self-improve.

    Never merge. Returns what the caller should do.
    """
    repo_p = Path(repo)
    if not is_source_checkout(repo_p):
        return {
            "action": "replace",
            "reason": "packaged_or_pip",
            "merge": False,
        }
    from remedy.execution.sandbox import run_unattended_git

    code, out, _err = run_unattended_git(repo_p, "status", "--porcelain", timeout=15)
    if code != 0:
        return {"action": "abort", "reason": "git_status_failed", "merge": False}
    if (out or "").strip():
        return {
            "action": "abort_dirty",
            "reason": "local_self_improve_or_wip",
            "merge": False,
            "note": (
                "Origin wins. Ship or discard local self-improve "
                "(`git reset --hard` / clean tree) before taking official update. "
                "Do not merge per-client LLM patches."
            ),
        }
    return {"action": "pull", "reason": "clean_source", "merge": False}

"""Submit a local-green draft as a GitHub *issue comment* — no branch, no release.

One standing inbox issue (`self-improve-inbox`). Each approved draft is a
comment with a scanned patch. GitHub stays uncluttered; you apply what is
worthwhile locally.

Security
--------
- Idle ticks never call this.
- Auto cannot authorize it (owner must click).
- ``gh`` viewerPermission must be WRITE/MAINTAIN/ADMIN. Random clients
  with only READ can file issues on a public repo — we refuse them.
- Both local scanners must pass before any ``gh`` write.
- Never ``git push``, never ``gh pr``, never ``gh release``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.self_inject_draft import (
    is_source_checkout,
    read_pending_ship,
)
from remedy.core.self_inject_guard import run_both_passes

TOOL_NAME = "self_improve_submit_issue"
# Legacy name still owner-locked so old banners cannot be Auto-waived.
OWNER_LOCK_ALIASES = frozenset({TOOL_NAME, "self_improve_submit_pr"})
INBOX_TITLE = "Remedy self-improve inbox"
INBOX_LABEL = "self-improve-inbox"
MAX_COMMENT_CHARS = 50_000
_WRITE_PERMS = frozenset({"ADMIN", "MAINTAIN", "WRITE"})


def approval_required_for_submit(
    command: str,
    session_id: str | None,
    *,
    reason: str,
) -> str | None:
    """Always prompt. Auto mode does **not** waive GitHub writes."""
    from remedy.core.approvals import APPROVALS

    if APPROVALS.is_approved(TOOL_NAME, command, session_id=session_id):
        return None
    item = APPROVALS.create(
        tool_name=TOOL_NAME,
        command=command,
        reason=reason,
        session_id=session_id,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={reason}\n"
        f"command={command}\n"
        "This posts a patch to the GitHub self-improve inbox (an issue comment). "
        "No branch, no release. Approve in UI then retry."
    )


def inbox_state_path(home: str | Path | None = None) -> Path:
    from remedy.core.self_inject import _home_dir

    return _home_dir(home) / "self_improve_inbox.json"


def read_inbox_state(home: str | Path | None = None) -> dict[str, Any] | None:
    path = inbox_state_path(home)
    if not path.is_file():
        return None
    with suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("number"):
            return data
    return None


def write_inbox_state(home: str | Path | None, payload: dict[str, Any]) -> Path:
    path = inbox_state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload, ensure_ascii=False)
    return path


def _safe_text(raw: Any, limit: int = 200) -> str:
    text = re.sub(r"[`\n\r\x00]", " ", str(raw or "")).strip()
    return text[:limit] or "—"


def payload_fingerprint(pending: dict[str, Any], diff: str) -> str:
    """Bind Approve to this exact patch (files + diff), not just the tool name."""
    blob = json.dumps(
        {
            "round": pending.get("round_id"),
            "changed": list(pending.get("changed") or []),
            "diff": diff or "",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def format_inbox_comment(
    pending: dict[str, Any],
    diff: str,
    scan: dict[str, Any],
) -> str:
    """Build the issue-comment body (no git refs)."""
    changed = pending.get("changed") or []
    files = "\n".join(f"- `{_safe_text(p, 120)}`" for p in changed) or "- (none)"
    patch = (diff or "").strip()
    # Do not let a crafted hunk close the markdown fence.
    patch = patch.replace("```", "'''")
    note = ""
    if len(patch) > 35_000:
        patch = patch[:35_000].rstrip() + "\n... [truncated]"
        note = "\n_Diff truncated for GitHub comment size._\n"
    fence = f"```diff\n{patch}\n```" if patch else "_No textual diff (binary or empty)._"
    return (
        f"## Draft `{_safe_text(pending.get('round_id'), 24)}`\n\n"
        f"{_safe_text(pending.get('summary'), 240)}\n\n"
        f"- scanners: {'clean' if scan.get('ok') else 'FAIL'}\n"
        f"- not a PR, not a branch, not a release\n\n"
        f"### Files\n{files}\n"
        f"{note}\n"
        f"### Patch\n{fence}\n"
    )[:MAX_COMMENT_CHARS]


def _git(repo: Path, *args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    from remedy.execution.sandbox import run_unattended_git

    return run_unattended_git(repo, *args, timeout=timeout)


async def _run_exec(
    repo: Path, argv: list[str], *, timeout: float
) -> tuple[int, str, str]:
    from remedy.execution.sandbox import unattended_vcs_env

    env = unattended_vcs_env(argv)
    try:
        from remedy.execution.process import create_hidden_subprocess_exec

        proc = await create_hidden_subprocess_exec(
            *argv,
            cwd=str(repo),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            with suppress(Exception):
                proc.kill()
            return 124, "", f"timeout {argv[:3]}"
        return (
            int(proc.returncode or 0),
            (out_b or b"").decode("utf-8", "replace"),
            (err_b or b"").decode("utf-8", "replace"),
        )
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


async def viewer_can_write(repo: Path) -> tuple[bool, str]:
    """True only for WRITE/MAINTAIN/ADMIN on this remote (not random READ users)."""
    code, out, err = await _run_exec(
        repo,
        ["gh", "repo", "view", "--json", "viewerPermission", "-q", ".viewerPermission"],
        timeout=30.0,
    )
    perm = (out or "").strip().upper()
    if code != 0:
        return False, (err or out or "gh repo view failed")[:240]
    return perm in _WRITE_PERMS, perm or "UNKNOWN"


async def _verify_inbox(repo: Path, number: int) -> dict[str, Any] | None:
    """Confirm *number* is the standing inbox on this repo (not a hijacked id)."""
    if number < 1 or number > 10_000_000:
        return None
    code, out, err = await _run_exec(
        repo,
        ["gh", "issue", "view", str(number), "--json", "title,url,state,number"],
        timeout=30.0,
    )
    if code != 0:
        return None
    with suppress(Exception):
        data = json.loads(out)
        title = str(data.get("title") or "")
        if INBOX_TITLE.lower() not in title.lower():
            return None
        if str(data.get("state") or "").upper() == "CLOSED":
            return None
        num = int(data.get("number") or number)
        return {"number": num, "url": str(data.get("url") or "")}
    _ = err
    return None


async def _ensure_inbox(
    repo: Path, home: str | Path | None
) -> dict[str, Any]:
    cached = read_inbox_state(home)
    if cached and cached.get("number"):
        with suppress(Exception):
            verified = await _verify_inbox(repo, int(cached["number"]))
            if verified:
                return verified
        # Cached id is stale or hijacked — drop it.
        with suppress(Exception):
            inbox_state_path(home).unlink(missing_ok=True)
    listed = await _run_exec(
        repo,
        [
            "gh",
            "issue",
            "list",
            "--label",
            INBOX_LABEL,
            "--state",
            "open",
            "--json",
            "number,url,title",
            "--limit",
            "5",
        ],
        timeout=45.0,
    )
    if listed[0] == 0 and (listed[1] or "").strip().startswith("["):
        with suppress(Exception):
            rows = json.loads(listed[1])
            for row in rows or []:
                if INBOX_TITLE.lower() in str(row.get("title") or "").lower():
                    verified = await _verify_inbox(repo, int(row["number"]))
                    if verified:
                        write_inbox_state(home, verified)
                        return verified
    with suppress(Exception):
        await _run_exec(
            repo,
            [
                "gh",
                "label",
                "create",
                INBOX_LABEL,
                "--description",
                "Standing self-improve inbox (comments only)",
                "--color",
                "5319E7",
            ],
            timeout=30.0,
        )
    create_cmd = [
        "gh",
        "issue",
        "create",
        "--title",
        INBOX_TITLE,
        "--body",
        (
            "Standing inbox for local Remedy self-improve drafts.\n\n"
            "Each comment is a scanned patch. Not a PR, not a branch, "
            "not a release. Owner applies anything worthwhile by hand."
        ),
    ]
    # Label is optional — first run may not have it yet.
    create_cmd.extend(["--label", INBOX_LABEL])
    created = await _run_exec(repo, create_cmd, timeout=45.0)
    if created[0] != 0 and "label" in (created[1] + created[2]).lower():
        created = await _run_exec(repo, create_cmd[:-2], timeout=45.0)
    blob = (created[1] + created[2]).strip()
    url = ""
    m = re.search(r"https://github\.com/\S+/issues/\d+", blob)
    if m:
        url = m.group(0).rstrip(").,")
    num = 0
    m2 = re.search(r"/issues/(\d+)", url or blob)
    if m2:
        num = int(m2.group(1))
    if created[0] != 0 or not num:
        raise RuntimeError(blob[:500] or "failed to create inbox issue")
    verified = await _verify_inbox(repo, num)
    st = verified or {"number": num, "url": url}
    write_inbox_state(home, st)
    return st


async def submit_self_improve_issue(
    runtime: Any,
    *,
    home: str | Path | None = None,
    repo: str | Path | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Post one inbox comment. No branch, no PR, no release."""
    from remedy.core.self_inject import _guess_repo

    home = home or getattr(runtime, "home_dir", None) or getattr(
        getattr(runtime, "config", None), "home_dir", None
    )
    repo_p = Path(repo) if repo else _guess_repo(runtime)
    if repo_p is None or not is_source_checkout(repo_p):
        return {
            "ok": False,
            "error": "not_source_checkout",
            "note": "Packaged clients do not post to GitHub.",
        }
    pending = read_pending_ship(home)
    if not pending or not pending.get("changed"):
        return {"ok": False, "error": "no_pending_ship"}

    from remedy.core.self_inject_guard import normalize_rel, path_allowed

    raw_changed = [str(p) for p in (pending.get("changed") or [])]
    changed: list[str] = []
    for p in raw_changed:
        n = normalize_rel(p)
        if not n or not path_allowed(n):
            return {
                "ok": False,
                "error": "unsafe_pending_path",
                "path": p,
            }
        dest = repo_p / n
        if dest.is_symlink():
            return {"ok": False, "error": "symlink_rejected", "path": n}
        with suppress(OSError):
            from remedy.core.self_inject_guard import MAX_FILE_BYTES

            if dest.is_file() and dest.stat().st_size > MAX_FILE_BYTES:
                return {"ok": False, "error": "file_too_large", "path": n}
        changed.append(n)
    pending = dict(pending)
    pending["changed"] = changed
    code_d, diff, err_d = _git(repo_p, "diff", "HEAD", "--", *changed)
    if code_d != 0:
        return {"ok": False, "error": "git_diff_failed", "detail": err_d[:400]}
    scan = run_both_passes(changed, diff, from_fork=False)
    if not scan["ok"]:
        return {
            "ok": False,
            "error": "local_guard_failed",
            "scan": scan,
            "note": "Refusing to post a patch the scanners would reject.",
        }

    can, perm = await viewer_can_write(repo_p)
    if not can:
        return {
            "ok": False,
            "error": "no_repo_write",
            "permission": perm,
            "note": (
                "Only owner/collaborators (WRITE+) may post to the inbox. "
                "Random clients with READ cannot use this tool to spam issues."
            ),
        }

    digest = payload_fingerprint(pending, diff)
    preview = (
        f"gh issue comment inbox draft={pending.get('round_id')} sha={digest}"
    )
    blocked = approval_required_for_submit(
        preview,
        session_id,
        reason=(
            "Post a self-improve patch as a comment on the standing GitHub "
            "inbox issue. No new branch, no PR, no release."
        ),
    )
    if blocked:
        return {"ok": False, "error": "approval_required", "banner": blocked}

    # Re-check write after Approve (token/login may have changed).
    can2, perm2 = await viewer_can_write(repo_p)
    if not can2:
        return {"ok": False, "error": "no_repo_write", "permission": perm2}

    try:
        inbox = await _ensure_inbox(repo_p, home)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "inbox_failed", "detail": str(exc)[:400]}

    import tempfile

    body = format_inbox_comment(pending, diff, scan)
    tmp: Path | None = None
    posted: tuple[int, str, str] = (1, "", "not posted")
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(body)
            tmp = Path(fh.name)
        posted = await _run_exec(
            repo_p,
            [
                "gh",
                "issue",
                "comment",
                str(inbox["number"]),
                "--body-file",
                str(tmp),
            ],
            timeout=45.0,
        )
    finally:
        if tmp is not None:
            with suppress(Exception):
                tmp.unlink(missing_ok=True)
    blob = (posted[1] + posted[2]).strip()
    if posted[0] != 0:
        return {
            "ok": False,
            "error": "comment_failed",
            "detail": blob[:500],
            "inbox": inbox,
        }
    url = inbox.get("url") or ""
    return {
        "ok": True,
        "kind": "inbox_comment",
        "issue": inbox.get("number"),
        "url": url,
        "round_id": pending.get("round_id"),
        "branch": None,
        "pr": False,
        "release": False,
        "note": "Posted to the standing inbox issue. Apply locally if you want it.",
    }


# Back-compat name used by older tests / tool wiring.
submit_self_improve_pr = submit_self_improve_issue

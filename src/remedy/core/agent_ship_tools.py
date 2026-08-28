"""Ship tools — git status/diff/log/push and GitHub release without shell thrash.

Partner path after green verify:
  git_status → git_diff / git_log (read) → git_push → gh_release (if tag/release goal)

Keeps credentials via scrub allowlist (GH_TOKEN) and sticky VCS approvals.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any


def approval_required_for_ship(
    command: str,
    session_id: str | None,
    *,
    reason: str,
) -> str | None:
    """Return an APPROVAL_REQUIRED blob, or None when Auto / already approved.

    ``git_push`` / ``gh_release`` used to force a prompt with
    ``needs_ask(...) or "git push (ship)"`` — Auto returned None, then the
    ``or`` fallback still created a banner. Full owner power must skip that.
    """
    from remedy.core.approvals import APPROVALS

    ask = APPROVALS.needs_ask(command, tool_name="bash_exec")
    if not ask:
        return None
    if APPROVALS.is_approved("bash_exec", command, session_id=session_id):
        return None
    item = APPROVALS.create(
        tool_name="bash_exec",
        command=command,
        reason=ask or reason,
        session_id=session_id,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={ask or reason}\n"
        f"command={command}\n"
        "Approve in UI then retry."
    )


def register_ship_tools(runtime: Any) -> None:
    """Register git_status / git_diff / git_log / git_push / gh_release / ship_status."""

    def _project() -> str:
        try:
            return str(runtime.effective_project_path() or "")
        except Exception:
            return ""

    def _mark_build(ship_pushed: bool | None = None, ship_released: bool | None = None,
                    ship_url: str = "", release_url: str = "") -> None:
        with contextlib.suppress(Exception):
            from remedy.core.build_engine import get_build_state

            st = get_build_state(runtime)
            if st is None:
                return
            if ship_pushed is True:
                st.ship_pushed = True
                if st.phase not in ("done",):
                    st.phase = "ship" if not st.ship_complete() else "done"
            if ship_released is True:
                st.ship_released = True
            if ship_url:
                st.ship_url = ship_url[:300]
            if release_url:
                st.ship_release_url = release_url[:300]
            if st.ship_complete() and st.last_verify_ok is True:
                st.phase = "done"

    async def _run_git(args: list[str], *, timeout: float = 120.0) -> tuple[int, str, str]:
        import asyncio
        import os
        import subprocess
        from pathlib import Path

        from remedy.execution.sandbox import scrub_subprocess_env

        proj = _project()
        cwd = Path(proj).expanduser() if proj else Path.cwd()
        if cwd.is_file():
            cwd = cwd.parent
        env = scrub_subprocess_env(argv=["git"])
        # Ensure git finds same identity as interactive
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags if os.name == "nt" else 0,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    proc.kill()
                return 124, "", f"timeout after {timeout}s"
            out = (out_b or b"").decode("utf-8", errors="replace")
            err = (err_b or b"").decode("utf-8", errors="replace")
            return int(proc.returncode or 0), out, err
        except FileNotFoundError:
            return 127, "", "git not found on PATH"
        except Exception as e:
            return 1, "", str(e)

    async def _run_gh(args: list[str], *, timeout: float = 180.0) -> tuple[int, str, str]:
        import asyncio
        import os
        from pathlib import Path

        from remedy.execution.sandbox import scrub_subprocess_env

        proj = _project()
        cwd = Path(proj).expanduser() if proj else Path.cwd()
        if cwd.is_file():
            cwd = cwd.parent
        env = scrub_subprocess_env(argv=["gh"])
        env.setdefault("GH_PROMPT_DISABLED", "1")
        creationflags = 0
        if os.name == "nt":
            import subprocess

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                *args,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags if os.name == "nt" else 0,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    proc.kill()
                return 124, "", f"timeout after {timeout}s"
            out = (out_b or b"").decode("utf-8", errors="replace")
            err = (err_b or b"").decode("utf-8", errors="replace")
            return int(proc.returncode or 0), out, err
        except FileNotFoundError:
            return 127, "", "gh CLI not found on PATH"
        except Exception as e:
            return 1, "", str(e)

    async def git_status() -> str:
        """Show branch, tracking, and short status (ship precondition)."""
        (code, out, err), (code2, log, _), (code3, rem, _) = await asyncio.gather(
            _run_git(["status", "-sb"]),
            _run_git(["log", "-3", "--oneline"]),
            _run_git(["remote", "-v"]),
        )
        lines = [
            f"**git_status** exit={code}",
            out.strip() or "(empty)",
        ]
        if err.strip():
            lines.append("stderr: " + err.strip()[:400])
        if code2 == 0 and log.strip():
            lines.append("recent:")
            lines.append(log.strip())
        if code3 == 0 and rem.strip():
            lines.append("remotes:")
            lines.append(rem.strip()[:400])
        return "\n".join(lines)[:3500]

    async def git_diff(staged: bool = False, path: str = "") -> str:
        """Show the unstaged (or staged) patch. Read-only — no approval."""
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        loc = (path or "").strip()
        if loc:
            args.extend(["--", loc])
        code, out, err = await _run_git(args, timeout=60.0)
        if code == 127:
            return "git_diff: git not found on PATH"
        blob = (out or "").strip()
        if not blob:
            extra = (err or "").strip()
            if extra:
                return f"git_diff exit={code}\n{extra[:800]}"
            where = "staged" if staged else "unstaged"
            return f"git_diff: no {where} changes" + (f" in {loc}" if loc else "")
        if len(blob) > 12_000:
            overflow = len(blob) - 12_000
            blob = blob[:12_000] + f"\n…[truncated {overflow} chars; pass path=]"
        return f"**git_diff** exit={code} staged={bool(staged)}\n{blob}"

    async def git_log(limit: int = 20, path: str = "", oneline: bool = True) -> str:
        """Show recent commits. Read-only — no approval."""
        try:
            n = max(1, min(100, int(limit or 20)))
        except (TypeError, ValueError):
            n = 20
        args = ["log", f"-{n}", "--no-color"]
        if oneline:
            args.append("--oneline")
        else:
            args.extend(["--format=%h %ad %an %s", "--date=short"])
        loc = (path or "").strip()
        if loc:
            args.extend(["--", loc])
        code, out, err = await _run_git(args, timeout=30.0)
        if code == 127:
            return "git_log: git not found on PATH"
        blob = (out or "").strip()
        if not blob:
            extra = (err or "").strip()
            if extra:
                return f"git_log exit={code}\n{extra[:800]}"
            return "git_log: no commits" + (f" in {loc}" if loc else "")
        if len(blob) > 8_000:
            overflow = len(blob) - 8_000
            blob = blob[:8_000] + f"\n…[truncated {overflow} chars; lower limit=]"
        return f"**git_log** exit={code} limit={n}\n{blob}"

    async def git_push(
        remote: str = "origin",
        ref: str = "HEAD",
        set_upstream: bool = True,
    ) -> str:
        """Push current branch to remote (uses sticky VCS approval family)."""
        from remedy.core.turn_context import turn_session_id

        rem = (remote or "origin").strip() or "origin"
        rf = (ref or "HEAD").strip() or "HEAD"
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.extend([rem, rf])
        cmd_preview = "git " + " ".join(args)
        sid = turn_session_id(runtime)
        blocked = approval_required_for_ship(
            cmd_preview, sid, reason="git push (ship)"
        )
        if blocked:
            return blocked
        code, out, err = await _run_git(args, timeout=180.0)
        blob = (out + "\n" + err).strip()
        ok = code == 0
        if ok:
            _mark_build(ship_pushed=True)
            # Infer remote URL
            _, rem_out, _ = await _run_git(["remote", "get-url", rem])
            url = (rem_out or "").strip()
            if url:
                _mark_build(ship_url=url)
            return (
                f"git_push ok ship_pushed=true remote={rem} ref={rf}\n"
                f"{blob[:2000]}\n"
                f"remote_url={url or '—'}"
            )
        low = blob.lower()
        if any(
            x in low
            for x in (
                "authentication failed",
                "could not read username",
                "permission denied",
                "could not read password",
            )
        ):
            with contextlib.suppress(Exception):
                from remedy.core.build_engine import get_build_state

                st = get_build_state(runtime)
                if st is not None:
                    st.wasted_auth_probes += 1
        return f"git_push FAILED exit={code}\n{blob[:2500]}"

    async def gh_release(
        tag: str = "",
        title: str = "",
        notes: str = "",
        generate_notes: bool = True,
        draft: bool = False,
        prerelease: bool = False,
    ) -> str:
        """Create a GitHub release via gh CLI (after green + push)."""
        from remedy.core.turn_context import turn_session_id

        tg = (tag or "").strip()
        if not tg:
            # Default: v from latest tag or v0.0.0-dev
            code, out, _ = await _run_git(["describe", "--tags", "--abbrev=0"])
            if code == 0 and out.strip():
                tg = out.strip()
            else:
                return (
                    "tag= required (e.g. v1.2.0). No existing tags found to reuse. "
                    "Pass tag= explicitly."
                )
        # Ensure tag exists locally (lightweight)
        code_t, _, err_t = await _run_git(["rev-parse", tg])
        if code_t != 0:
            # Pushing a tag is a push. It used to happen here before the
            # release approval below was consulted, so gh_release could put a
            # tag on the remote in Ask mode without ever showing the owner a
            # prompt — the one thing git_push exists to prevent. Gate it on
            # the same sticky VCS family, ahead of any mutation.
            tag_blocked = approval_required_for_ship(
                f"git push origin {tg}",
                turn_session_id(runtime),
                reason="git push tag (ship)",
            )
            if tag_blocked:
                return tag_blocked
            # Create annotated-ish lightweight tag on HEAD
            await _run_git(["tag", tg])
            # Push tag
            await _run_git(["push", "origin", tg], timeout=120.0)

        args = ["release", "create", tg]
        if title.strip():
            args.extend(["--title", title.strip()[:120]])
        else:
            args.extend(["--title", tg])
        if notes.strip():
            args.extend(["--notes", notes.strip()[:4000]])
        elif generate_notes:
            args.append("--generate-notes")
        else:
            args.extend(["--notes", f"Release {tg}"])
        if draft:
            args.append("--draft")
        if prerelease:
            args.append("--prerelease")

        cmd_preview = "gh " + " ".join(args[:6])
        sid = turn_session_id(runtime)
        blocked = approval_required_for_ship(
            cmd_preview, sid, reason="gh release create (ship)"
        )
        if blocked:
            return blocked

        code, out, err = await _run_gh(args, timeout=180.0)
        blob = (out + "\n" + err).strip()
        if code == 0:
            url = ""
            m = re.search(r"https://github\.com/\S+", blob)
            if m:
                url = m.group(0).rstrip(").,")
            _mark_build(ship_released=True, release_url=url)
            # Push usually already done; treat release as ship progress
            _mark_build(ship_pushed=True)
            return (
                f"gh_release ok ship_released=true tag={tg}\n"
                f"url={url or '—'}\n"
                f"{blob[:2000]}"
            )
        low = blob.lower()
        if "already exists" in low:
            # Fetch existing URL
            c2, o2, _ = await _run_gh(["release", "view", tg, "--json", "url", "-q", ".url"])
            url = (o2 or "").strip()
            _mark_build(ship_released=True, release_url=url, ship_pushed=True)
            return (
                f"gh_release ok (already exists) ship_released=true tag={tg}\n"
                f"url={url or '—'}\n{blob[:800]}"
            )
        if "auth" in low or "login" in low or "token" in low:
            with contextlib.suppress(Exception):
                from remedy.core.build_engine import get_build_state

                st = get_build_state(runtime)
                if st is not None:
                    st.wasted_auth_probes += 1
        return f"gh_release FAILED exit={code}\n{blob[:2500]}"

    async def ship_status() -> str:
        """Show ship phase flags from live build engine + last report."""
        from remedy.core.build_engine import get_build_state

        st = get_build_state(runtime)
        if st is None or not st.active:
            return "ship_status: no active build turn"
        rep = st.ship_report()
        lines = [
            "**ship_status**",
            f"phase={rep.get('phase')} verify_ok={rep.get('verify_ok')}",
            f"ship_required={rep.get('ship_required')} "
            f"pushed={rep.get('ship_pushed')} released={rep.get('ship_released')}",
            f"ship_url={rep.get('ship_url') or '—'}",
            f"release_url={rep.get('ship_release_url') or '—'}",
            f"verify_command={rep.get('verify_command') or '—'}",
            f"wasted_auth_probes={rep.get('wasted_auth_probes')}",
        ]
        if rep.get("paths"):
            lines.append("paths: " + ", ".join(rep["paths"][-8:]))
        return "\n".join(lines)

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "git_status",
        "Ship: git status -sb + recent log + remotes. Use before git_push.",
        git_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "git_diff",
        "Read-only git diff (unstaged working tree, or staged=true). "
        "path= limits to one file. Use before git_push or to review edits.",
        git_diff,
        {
            "type": "object",
            "properties": {
                "staged": {"type": "boolean"},
                "path": {"type": "string"},
            },
        },
    )
    reg.register_builtin_handler(
        "git_log",
        "Read-only git log (default 20 oneline commits). "
        "path= limits to one file. Use to see who changed what before editing.",
        git_log,
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "path": {"type": "string"},
                "oneline": {"type": "boolean"},
            },
        },
    )
    reg.register_builtin_handler(
        "git_push",
        "Ship: push current branch (default origin HEAD -u). Sticky VCS approval. "
        "Use after green verify — do not re-run pytest first.",
        git_push,
        {
            "type": "object",
            "properties": {
                "remote": {"type": "string"},
                "ref": {"type": "string"},
                "set_upstream": {"type": "boolean"},
            },
            "required": [],
        },
    )
    reg.register_builtin_handler(
        "gh_release",
        "Ship: gh release create (tag required unless reusing). Push tag if needed. "
        "generate_notes=true by default. After green + git_push.",
        gh_release,
        {
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "generate_notes": {"type": "boolean"},
                "draft": {"type": "boolean"},
                "prerelease": {"type": "boolean"},
            },
            "required": [],
        },
    )
    reg.register_builtin_handler(
        "ship_status",
        "Ship: live build engine ship flags (pushed/released/urls).",
        ship_status,
        {"type": "object", "properties": {}},
    )

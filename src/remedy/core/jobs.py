"""Silent internal jobs — subagent power without multi-agent chat.

Jobs run tool subsets in-process and return a text summary to the parent ReAct
loop. They never open a second chat personality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class JobResult:
    kind: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def _resolve_job_path(
    runtime: Any, path: str = ".", *, for_write: bool = False
) -> Path:
    """Resolve path under runtime access scope. Fail closed (no raw absolute escape).

    Pass ``for_write=True`` for shell/mutation job workdirs so project scope
    cannot escape via Desktop/Documents/Downloads read roots.
    """
    raw = (path or ".").strip() or "."
    try:
        return runtime.resolve_tool_path(raw, for_write=for_write)
    except TypeError:
        # Older mock runtimes without for_write kwarg.
        return runtime.resolve_tool_path(raw)


async def run_explore_job(
    runtime: Any,
    *,
    query: str = "",
    path: str = ".",
    max_files: int = 40,
) -> JobResult:
    """Read-only survey: tree + fingerprint + orientation + optional search."""
    from remedy.core.project_fingerprint import fingerprint_path, orientation_block
    from remedy.core.repo_search import format_hits, search_repo_async

    root = runtime.effective_project_path()
    try:
        target = _resolve_job_path(runtime, path)
    except Exception as e:
        return JobResult(
            kind="explore",
            ok=False,
            summary=f"error: path not allowed or unresolvable: {path} ({e})",
            details={"path": path},
        )
    parts: list[str] = [f"Explore job under {target}"]

    def _survey() -> list[str]:
        extra: list[str] = []
        try:
            orient_root = target if target.is_dir() else target.parent
            fp = fingerprint_path(orient_root)
            fp_lines = fp.context_lines()
            if fp_lines:
                extra.append("\n".join(fp_lines))
            orient = orientation_block(orient_root)
            if orient:
                extra.append(orient)
        except Exception as e:
            extra.append(f"fingerprint/orientation error: {e}")

        try:
            if target.is_dir():
                entries = sorted(
                    target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                )
                visible = [p for p in entries if not p.name.startswith(".")][:max_files]
                lines = []
                for p in visible:
                    try:
                        rel = p.relative_to(root).as_posix()
                    except ValueError:
                        try:
                            rel = p.relative_to(target).as_posix()
                        except ValueError:
                            rel = p.name
                    lines.append(f"{'dir ' if p.is_dir() else 'file'} {rel}")
                extra.append(
                    "Listing:\n" + ("\n".join(lines) if lines else "(empty)")
                )

                sub_lines: list[str] = []
                for p in visible:
                    if not p.is_dir():
                        continue
                    try:
                        kids = sorted(
                            x for x in p.iterdir() if not x.name.startswith(".")
                        )[:8]
                    except OSError:
                        continue
                    if not kids:
                        continue
                    try:
                        pref = p.relative_to(target).as_posix()
                    except ValueError:
                        pref = p.name
                    names = ", ".join(
                        (k.name + "/") if k.is_dir() else k.name for k in kids
                    )
                    sub_lines.append(f"  {pref}/ → {names}")
                    if len(sub_lines) >= 12:
                        break
                if sub_lines:
                    extra.append("Subdirs (sample):\n" + "\n".join(sub_lines))
            elif target.is_file():
                try:
                    rel = target.relative_to(root).as_posix()
                except ValueError:
                    rel = str(target)
                extra.append(f"File: {rel}")
            else:
                extra.append(f"path not found: {target}")
        except Exception as e:
            extra.append(f"list error: {e}")
        return extra

    parts.extend(await asyncio.to_thread(_survey))

    if (query or "").strip():
        if not target.exists():
            parts.append(f"error: path not found for search: {target}")
        else:
            search_path = str(target)
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            roots = None
            scope = "project"
            try:
                roots = runtime.allowed_roots()
                scope = runtime.access_scope()
            except Exception:
                pass
            hits, engine = await search_repo_async(
                root,
                query.strip(),
                path=search_path,
                max_matches=30,
                home_dir=home,
                allowed_roots=roots,
                access_scope=scope,
            )
            parts.append(format_hits(hits, engine=engine, pattern=query.strip()))
    elif not any(p.startswith("Listing:") or p.startswith("File:") for p in parts):
        parts.append("(no query — pass query= to search)")

    summary = "\n\n".join(parts)
    return JobResult(
        kind="explore",
        ok=True,
        summary=summary[:14_000],
        details={"path": str(target)},
    )


async def run_verify_job(
    runtime: Any,
    *,
    command: str = "",
    path: str = "",
    timeout: float = 180.0,
) -> JobResult:
    """Run a verify/test command via sandbox (optional workdir path)."""
    from remedy.core.project_fingerprint import fingerprint_path, path_env_with_local_bins
    from remedy.core.security import check_dangerous_command
    from remedy.execution.sandbox import SubprocessSandbox

    root = runtime.effective_project_path()
    workdir = root
    if (path or "").strip():
        try:
            # Shell verify workdir is a mutation surface — write roots only.
            workdir = _resolve_job_path(runtime, path, for_write=True)
            if workdir.is_file():
                workdir = workdir.parent
        except Exception as e:
            return JobResult(
                kind="verify",
                ok=False,
                summary=f"error: path not allowed or unresolvable: {path} ({e})",
                details={"path": path},
            )

    cmd = (command or "").strip()
    if not cmd:
        # Fingerprint-suggested verify when command omitted
        try:
            fp = await asyncio.to_thread(fingerprint_path, workdir)
            cmd = (fp.suggest_verify or "").strip()
        except Exception:
            cmd = ""
    if not cmd:
        return JobResult(
            kind="verify",
            ok=False,
            summary=(
                "No verify command provided and no stack fingerprint default. "
                "Pass command= (e.g. pytest -q) or path= to a known project tree."
            ),
        )
    danger = check_dangerous_command(["bash", "-c", cmd])
    if danger:
        return JobResult(
            kind="verify",
            ok=False,
            summary=f"Blocked by security policy: {danger}",
        )
    # Same Ask-mode gate as bash_exec — fail closed (never swallow and run shell).
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import turn_session_id

    try:
        ask_reason = APPROVALS.needs_ask(cmd, tool_name="bash_exec")
        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved(
            "bash_exec", cmd, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="bash_exec",
                command=cmd,
                reason=ask_reason,
                session_id=sid,
            )
            return JobResult(
                kind="verify",
                ok=False,
                summary=(
                    f"APPROVAL_REQUIRED id={item.id}\n"
                    f"reason={ask_reason}\n"
                    f"command={cmd[:400]}\n"
                    "Do not invent success. Tell the user this needs approval in the UI "
                    f"(or /approve {item.id}). After they approve, retry job_run/mission_verify."
                ),
                details={"approval_id": item.id},
            )
    except Exception as e:
        return JobResult(
            kind="verify",
            ok=False,
            summary=(
                f"APPROVAL_CHECK_FAILED: {e}\n"
                "Shell verify was not run (fail closed). Retry or use bash_exec after approval."
            ),
            details={"error": str(e)},
        )
    # Write roots only — never fall back to read roots (Desktop/Docs/Downloads).
    # Same project write jail as bash_exec so job_run/mission_verify cannot
    # bypass shell mutation controls via the silent job path.
    try:
        roots = list(runtime.write_roots() or [])
    except Exception as exc:
        return JobResult(
            kind="verify",
            ok=False,
            summary=(
                f"WRITE_JAIL: cannot resolve write roots for shell jail: {exc}\n"
                "Shell verify was not run (fail closed). Ensure a project folder is set."
            ),
            details={"error": str(exc)},
        )
    if not roots:
        roots = [root]
    try:
        bound = not bool(runtime.project_path_is_unset())
    except Exception:
        bound = True  # fail closed: treat as bound if unknown
    try:
        scope = str(runtime.access_scope() or "project")
    except Exception:
        scope = "project"
    try:
        from remedy.core.shell_write_jail import check_shell_write_jail

        jail_hit = check_shell_write_jail(
            cmd,
            write_roots=list(roots),
            cwd=workdir,
            project_bound=bound,
            access_scope=scope,
        )
    except Exception as exc:
        return JobResult(
            kind="verify",
            ok=False,
            summary=(
                f"WRITE_JAIL: shell write jail check failed (refused): {exc}\n"
                "Shell verify was not run (fail closed)."
            ),
            details={"error": str(exc)},
        )
    if jail_hit:
        return JobResult(
            kind="verify",
            ok=False,
            summary=(
                f"WRITE_JAIL: {jail_hit}\n"
                "job_run/mission_verify uses the same shell write jail as bash_exec. "
                "Stay inside the focus project with file_write/file_edit."
            ),
            details={"write_jail": jail_hit},
        )
    from remedy.execution.sandbox import allowed_paths_for_shell

    sandbox = SubprocessSandbox(allowed_paths=allowed_paths_for_shell(roots, workdir))
    from remedy.execution.host.runner import prepare_host_command

    prepared = prepare_host_command(cmd, project_path=root)
    argv = prepared.argv
    env = path_env_with_local_bins(workdir)
    timeout_s = max(5.0, min(600.0, float(timeout or 180)))
    result = await sandbox.execute(
        argv,
        workdir=workdir,
        timeout_seconds=timeout_s,
        env=env,
    )
    out = (result.stdout or "")[:4000]
    err = (result.stderr or "")[:2000]
    ok = result.exit_code == 0
    summary = (
        f"verify exit_code={result.exit_code}\n"
        f"cwd={workdir}\n"
        f"timeout_s={timeout_s}\n"
        f"command={cmd}\n"
        f"{out}"
        + (f"\nstderr:\n{err}" if err else "")
    )
    return JobResult(
        kind="verify",
        ok=ok,
        summary=summary,
        details={"exit_code": result.exit_code, "cwd": str(workdir)},
    )


async def run_diff_job(runtime: Any, *, path: str = ".") -> JobResult:
    """Git status + diff --stat under path (best-effort).

    Spawns ``git`` directly (hidden console) — not via ``cmd``/PowerShell — so
    spread/diff workers do not flash a window on Windows.
    """
    from remedy.execution.sandbox import SubprocessSandbox

    try:
        workdir = _resolve_job_path(runtime, path)
    except Exception as e:
        return JobResult(
            kind="diff",
            ok=False,
            summary=f"error: path not allowed or unresolvable: {path} ({e})",
            details={"path": path},
        )
    if workdir.is_file():
        workdir = workdir.parent
    from remedy.execution.sandbox import allowed_paths_for_shell

    roots = runtime.allowed_roots()
    sandbox = SubprocessSandbox(
        allowed_paths=allowed_paths_for_shell(
            roots or [runtime.effective_project_path()], workdir
        )
    )
    chunks: list[str] = []
    last_code = 0
    results = await asyncio.gather(
        sandbox.execute(["git", "status", "-sb"], workdir=workdir, timeout_seconds=60.0),
        sandbox.execute(["git", "diff", "--stat"], workdir=workdir, timeout_seconds=60.0),
        sandbox.execute(
            ["git", "diff", "--cached", "--stat"],
            workdir=workdir,
            timeout_seconds=60.0,
        ),
    )
    for result in results:
        last_code = result.exit_code
        part = ((result.stdout or "") + (result.stderr or "")).strip()
        if part:
            chunks.append(part)
    ok = last_code == 0 or any(chunks)
    body = "\n".join(chunks)[:6000]
    return JobResult(
        kind="diff",
        ok=ok,
        summary=f"git summary cwd={workdir}\nexit={last_code}\n{body}",
        details={"exit_code": last_code},
    )


async def run_job(
    runtime: Any,
    kind: str,
    *,
    query: str = "",
    path: str = ".",
    command: str = "",
    timeout: float = 180.0,
) -> JobResult:
    k = (kind or "explore").strip().lower()
    if k in ("explore", "survey", "map"):
        return await run_explore_job(runtime, query=query, path=path)
    if k in ("verify", "test", "check"):
        return await run_verify_job(
            runtime, command=command, path=path, timeout=timeout
        )
    if k in ("diff", "git", "status"):
        return await run_diff_job(runtime, path=path or ".")
    if k in ("implement",):
        return JobResult(
            kind="implement",
            ok=True,
            summary=(
                "Implement jobs use the main agent tools (file_edit, file_write, "
                "bash_exec). Continue implementing in this turn; use job_run "
                "kind=explore|verify|diff for support."
            ),
        )
    return JobResult(kind=k, ok=False, summary=f"Unknown job kind: {kind}")

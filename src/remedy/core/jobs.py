"""Silent internal jobs — subagent power without multi-agent chat.

Jobs run tool subsets in-process and return a text summary to the parent ReAct
loop. They never open a second chat personality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobResult:
    kind: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


async def run_explore_job(
    runtime: Any,
    *,
    query: str = "",
    path: str = ".",
    max_files: int = 40,
) -> JobResult:
    """Read-only survey: list_dir + optional repo_search."""
    from remedy.core.repo_search import format_hits, search_repo

    root = runtime.effective_project_path()
    parts: list[str] = [f"Explore job under {root}"]
    try:
        target = runtime.resolve_tool_path(path or ".")
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
                    rel = p.name
                lines.append(f"{'dir ' if p.is_dir() else 'file'} {rel}")
            parts.append("Listing:\n" + ("\n".join(lines) if lines else "(empty)"))
        elif target.is_file():
            try:
                rel = target.relative_to(root).as_posix()
            except ValueError:
                rel = str(target)
            parts.append(f"File: {rel}")
    except Exception as e:
        parts.append(f"list error: {e}")

    if (query or "").strip():
        hits, engine = search_repo(
            root, query.strip(), path=path or ".", max_matches=30
        )
        parts.append(format_hits(hits, engine=engine, pattern=query.strip()))
    elif not any(p.startswith("Listing:") or p.startswith("File:") for p in parts):
        parts.append("(no query — pass query= to search)")

    summary = "\n\n".join(parts)
    return JobResult(kind="explore", ok=True, summary=summary[:12_000])


async def run_verify_job(
    runtime: Any,
    *,
    command: str = "",
    timeout: float = 120.0,
) -> JobResult:
    """Run a verify/test command via sandbox."""
    from remedy.core.security import check_dangerous_command
    from remedy.execution.process import win_shell_prefix
    from remedy.execution.sandbox import SubprocessSandbox

    cmd = (command or "").strip()
    if not cmd:
        return JobResult(kind="verify", ok=False, summary="No verify command provided.")
    danger = check_dangerous_command(["bash", "-c", cmd])
    if danger:
        return JobResult(
            kind="verify",
            ok=False,
            summary=f"Blocked by security policy: {danger}",
        )
    root = runtime.effective_project_path()
    roots = runtime.allowed_roots()
    sandbox = SubprocessSandbox(allowed_paths=roots or [root])
    argv = [*win_shell_prefix(), cmd]
    result = await sandbox.execute(argv, workdir=root, timeout_seconds=float(timeout or 120))
    out = (result.stdout or "")[:4000]
    err = (result.stderr or "")[:2000]
    ok = result.exit_code == 0
    summary = (
        f"verify exit_code={result.exit_code}\n"
        f"command={cmd}\n"
        f"{out}"
        + (f"\nstderr:\n{err}" if err else "")
    )
    return JobResult(
        kind="verify",
        ok=ok,
        summary=summary,
        details={"exit_code": result.exit_code},
    )


async def run_job(
    runtime: Any,
    kind: str,
    *,
    query: str = "",
    path: str = ".",
    command: str = "",
) -> JobResult:
    k = (kind or "explore").strip().lower()
    if k in ("explore", "survey", "map"):
        return await run_explore_job(runtime, query=query, path=path)
    if k in ("verify", "test", "check"):
        return await run_verify_job(runtime, command=command)
    if k in ("implement",):
        # Implement jobs stay on the parent ReAct loop — return guidance.
        return JobResult(
            kind="implement",
            ok=True,
            summary=(
                "Implement jobs use the main agent tools (file_edit, file_write, "
                "bash_exec). Continue implementing in this turn; use job_run "
                "kind=explore|verify for support."
            ),
        )
    return JobResult(kind=k, ok=False, summary=f"Unknown job kind: {kind}")

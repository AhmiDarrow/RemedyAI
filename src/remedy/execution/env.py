"""Child-process environment scrub and workdir-root helpers.

Env scrub is CredentialBroker (M1.4). Workdir root lists feed Zig write-jail
via ``host_binding.write_jail_set_roots`` — Python does not twin the jail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def scrub_subprocess_env(
    env: dict[str, str] | None = None,
    *,
    grants: list[Any] | None = None,
    argv: list[str] | None = None,
) -> dict[str, str]:
    """Child env: safe OS/path only, plus explicit credential grants.

    Generic shell (no grants, no git/gh argv) does **not** inherit GH_TOKEN,
    SSH_AUTH_SOCK, or registry tokens. git/gh/npm argv infers a grant so
    ``git push`` / ``gh`` still work when those tools are the executable.
    """
    from remedy.credentials.broker import child_environment, grant_for_argv

    inferred = list(grants or [])
    if not inferred and argv:
        inferred = grant_for_argv(argv, source=env)
    return child_environment(env, grants=inferred)


def unattended_vcs_env(
    argv: list[str],
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """git/gh env: VCS tokens only, no prompt / GIT_ASKPASS / LLM keys."""
    out = scrub_subprocess_env(env, argv=argv)
    out["GIT_TERMINAL_PROMPT"] = "0"
    out["GH_PROMPT_DISABLED"] = "1"
    out["GCM_INTERACTIVE"] = "never"
    for key in list(out):
        if key.upper() == "GIT_ASKPASS":
            out.pop(key, None)
    return out


def run_unattended_git(
    repo: Path | str,
    *args: str,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Hidden git. Never prompts. Returns (code, stdout, stderr)."""
    import subprocess

    from remedy.execution.process import run_hidden

    try:
        proc = run_hidden(["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=unattended_vcs_env(["git"]),
            )
    except FileNotFoundError:
        return 127, "", "git not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git timeout"
    return int(proc.returncode or 0), proc.stdout or "", proc.stderr or ""


def allowed_paths_for_shell(
    roots: list[Path] | None,
    cwd: Path | None = None,
) -> list[Path]:
    """Workdir roots for Zig write-jail. Empty list = no workdir jail (Full)."""
    from remedy.core.approvals import is_full_approval

    if is_full_approval():
        return []
    out: list[Path] = list(roots or [])
    if cwd is not None and cwd not in out:
        out.append(cwd)
    return out

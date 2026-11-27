"""``remedy`` entry point: hand the command to the Go ``remedy-runtime``.

The CLI lives in the native runtime (``native/go/cli``). This launcher only
finds that binary (PATH, the wheel's cache, a checkout, or a one-time
checksum-verified download), sets the few things the binary cannot know
about this Python install, and runs it with the user's argv.

Commands not yet in the Go CLI, and a runtime too old to carry it, still run
the Python CLI so nothing stops working mid-move.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

#: Top-level commands the Go CLI answers. Grows as groups move over.
GO_COMMANDS = frozenset(
    {
        "auth",
        "chat",
        "config",
        "connect-relay",
        "desktop",
        "exec",
        "gateway",
        "handoff",
        "memory",
        "open",
        "serve",
        "session",
        "settings",
        "tool",
        "user",
    }
)


def command_word(argv: list[str]) -> str:
    """The subcommand in argv, skipping the global ``--home`` option."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--home":
            i += 2
            continue
        if tok.startswith("--home="):
            i += 1
            continue
        return tok
    return ""


def wants_go(argv: list[str]) -> bool:
    return command_word(argv) in GO_COMMANDS


def _runtime_command() -> tuple[list[str] | None, str | None]:
    """argv prefix for remedy-runtime and the core library it should load."""
    from remedy.interfaces.cli.cmd_runtime import resolve_remedy_runtime_command

    cmd = resolve_remedy_runtime_command()
    core: str | None = None
    try:
        from remedy.runtime.native_bootstrap import cached_core_lib

        hit = cached_core_lib()
        core = str(hit) if hit is not None else None
    except Exception:
        core = None
    if cmd:
        return cmd, core
    try:
        from remedy.runtime.native_bootstrap import (
            NativeBootstrapError,
            ensure_native_runtime,
        )
    except Exception:
        return None, None
    print(
        "Downloading a checksum-verified remedy-runtime from GitHub Releases (cached after this)...",
        file=sys.stderr,
    )
    try:
        installed = ensure_native_runtime()
    except NativeBootstrapError as exc:
        print(f"remedy: could not get remedy-runtime: {exc}", file=sys.stderr)
        return None, None
    print(
        f"Downloaded remedy-runtime v{installed.version} for {installed.triple} (checksum verified).",
        file=sys.stderr,
    )
    return [str(installed.runtime_bin)], str(installed.core_lib)


def _cwd_for(cmd: list[str]) -> str | None:
    """`go run ./cmd/remedy-runtime` from a checkout must run in native/go."""
    if len(cmd) >= 3 and cmd[1] == "run":
        from remedy.interfaces.cli.cmd_runtime import _repo_root

        root = _repo_root()
        if root is not None:
            return str(root / "native" / "go")
    return None


def child_env(core_lib: str | None) -> dict[str, str]:
    env = os.environ.copy()
    try:
        from remedy.runtime.native_bootstrap import package_version

        env.setdefault("REMEDY_CLI_VERSION", package_version())
    except Exception:
        pass
    if not env.get("REMEDY_BUNDLED_WEBUI_DIR", "").strip():
        try:
            from remedy.webui import bundled_webui_dir

            bundled = bundled_webui_dir()
            if bundled is not None:
                env["REMEDY_BUNDLED_WEBUI_DIR"] = str(bundled)
        except Exception:
            pass
    if core_lib and not env.get("REMEDY_NATIVE_CORE_LIB", "").strip():
        env["REMEDY_NATIVE_CORE_LIB"] = core_lib
    # Until the runtime starts no Python, it hands this interpreter to its
    # worker. abspath, not realpath: a venv python is often a symlink.
    if not env.get("REMEDY_PYTHON", "").strip() and sys.executable:
        env["REMEDY_PYTHON"] = os.path.abspath(sys.executable)
    return env


def runtime_has_cli(cmd: list[str], env: dict[str, str], cwd: str | None) -> bool:
    """True when this remedy-runtime build carries the CLI (probe says cli>=1)."""
    try:
        done = subprocess.run(
            [*cmd, "--probe"],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        record = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False
    return isinstance(record, dict) and int(record.get("cli") or 0) >= 1


def _python_main(argv: list[str]) -> None:
    from remedy.interfaces.cli.main import main as python_main

    python_main(argv)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not wants_go(args):
        _python_main(args)
        return
    cmd, core = _runtime_command()
    if not cmd:
        _python_main(args)
        return
    env = child_env(core)
    cwd = _cwd_for(cmd)
    if not runtime_has_cli(cmd, env, cwd):
        _python_main(args)
        return
    full = [*cmd, *args]
    if os.name == "posix" and cwd is None:
        # Replace this process: signals, the terminal and the exit code are
        # the runtime's own, with no Python left waiting in between.
        os.execve(full[0], full, env)
    try:
        code = subprocess.call(full, env=env, cwd=cwd)
    except KeyboardInterrupt:
        code = 130
    except OSError as exc:
        print(f"remedy: could not start remedy-runtime: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()

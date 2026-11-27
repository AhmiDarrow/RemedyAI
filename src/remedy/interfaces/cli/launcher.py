"""``remedy`` entry point: hand the command to the Go ``remedy-runtime``.

The CLI lives in the native runtime (``native/go/cli``). This launcher only
finds that binary (PATH, the wheel's cache, a checkout, or a one-time
checksum-verified download), sets the few things the binary cannot know
about this Python install, and runs it with the user's argv.

Every command runs in Go. There is no Python CLI behind this: a missing
runtime, or one too old to carry the CLI, is reported and nothing else runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from remedy.core.runtime_identity import is_frozen_install


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[3], here.parents[4] if len(here.parents) > 4 else None):
        if candidate is None:
            continue
        if (candidate / "native" / "go" / "go.mod").is_file():
            return candidate
    env = os.environ.get("REMEDY_DEV_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "native" / "go" / "go.mod").is_file():
            return root
    return None


def _runtime_bin_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("remedy-runtime.exe", "remedy-runtime")
    return ("remedy-runtime",)


def resolve_remedy_runtime_command() -> list[str] | None:
    """Return argv to launch ``remedy-runtime``, or None when missing.

    Search order: ``REMEDY_NATIVE_RUNTIME_BIN`` / ``REMEDY_RUNTIME``, PATH,
    ``sys.executable`` dir, repo ``desktop/bin``, then ``go run`` in a checkout.
    """
    for env_name in ("REMEDY_NATIVE_RUNTIME_BIN", "REMEDY_RUNTIME"):
        override = os.environ.get(env_name, "").strip()
        if override:
            return [override]

    for name in _runtime_bin_names():
        found = shutil.which(name)
        if found:
            return [found]

    # abspath, not Path.resolve(): a venv python is often a symlink, and
    # following it would look next to the system interpreter, missing a
    # runtime placed in the venv bin.
    roots: list[Path] = [Path(os.path.abspath(sys.executable)).parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    root = _repo_root()
    if root is not None:
        roots.append(root / "desktop" / "bin")
        roots.append(root / "bin")
    for base in roots:
        for name in _runtime_bin_names():
            candidate = base / name
            if candidate.is_file():
                return [str(candidate)]

    try:
        from remedy.runtime.native_bootstrap import cached_runtime_bin

        cached = cached_runtime_bin()
        if cached is not None:
            return [str(cached)]
    except Exception:
        pass

    if root is not None:
        go = shutil.which("go")
        if go:
            return [go, "run", "./cmd/remedy-runtime"]

    return None


def _runtime_command() -> tuple[list[str] | None, str | None]:
    """argv prefix for remedy-runtime and the core library it should load."""
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
    # update/uninstall drive pip with this interpreter and report this package
    # path. A frozen app leaves both unset, so Go only reports and clears data.
    if not is_frozen_install() and sys.executable:
        env.setdefault("REMEDY_CLI_PYTHON", os.path.abspath(sys.executable))
        try:
            import remedy

            env.setdefault(
                "REMEDY_CLI_PACKAGE_DIR", str(Path(remedy.__file__).resolve().parent)
            )
        except Exception:
            pass
    # Skill scripts written in Python run with this interpreter. abspath, not
    # realpath: a venv python is often a symlink.
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


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        args = ["--help"]  # the runtime alone with no arguments is not the CLI
    cmd, core = _runtime_command()
    if not cmd:
        print(
            "remedy: remedy-runtime was not found and could not be downloaded.\n"
            "  Install Remedy Desktop, or put remedy-runtime on PATH, then try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    env = child_env(core)
    cwd = _cwd_for(cmd)
    if not runtime_has_cli(cmd, env, cwd):
        print(
            f"remedy: {cmd[0]} is too old to carry the command line. Update Remedy, then try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)
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


def mcp_main() -> None:
    """``remedy-mcp``: the MCP stdio host, the same as ``remedy mcp serve``."""
    main(["mcp", "serve", *sys.argv[1:]])


if __name__ == "__main__":
    main()

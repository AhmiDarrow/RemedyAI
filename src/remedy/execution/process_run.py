"""run_hidden / popen_hidden facades over Zig spawn and exec-capture."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.execution.process_argv import resolve_argv0
from remedy.execution.process_child import DEFAULT_RUN_TIMEOUT_S, PipedProcess


def _stdio_is_pipe_request(
    *,
    capture_output: bool = False,
    input: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> bool:
    if capture_output or input is not None:
        return True
    pipe_markers = (subprocess.PIPE, asyncio.subprocess.PIPE)
    for value in (stdout, stderr, stdin):
        if value in pipe_markers:
            return True
    extra = extra or {}
    return any(extra.get(key) in pipe_markers for key in ("stdout", "stderr", "stdin"))


def _can_exec_capture(
    *,
    capture_output: bool,
    input: Any,
    extra: Mapping[str, Any],
) -> bool:
    if not capture_output or input is not None:
        return False
    pipe_markers = (subprocess.PIPE, asyncio.subprocess.PIPE)
    return not any(extra.get(key) in pipe_markers for key in ("stdout", "stderr", "stdin"))


def run_hidden(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = DEFAULT_RUN_TIMEOUT_S,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input: str | bytes | None = None,
    **extra: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run *args* via Zig authorized spawn or exec-capture (no soft pipes)."""
    from remedy.execution import process as P

    P.require_process_host()
    if not _stdio_is_pipe_request(
        capture_output=capture_output, input=input, extra=extra
    ):
        child = P.spawn_hidden(args, cwd=cwd, env=env)
        try:
            # Route through process.wait so tests can monkeypatch the public API.
            code = P.wait(child, timeout)
            if code is None:
                child.kill_tree()
                child.close()
                raise subprocess.TimeoutExpired(
                    cmd=list(args),
                    timeout=0.0 if timeout is None else float(timeout),
                )
            empty: str | bytes = "" if text else b""
            argv = [str(a) for a in args]
            result = subprocess.CompletedProcess(argv, int(code), empty, empty)
            if check and result.returncode:
                raise subprocess.CalledProcessError(
                    result.returncode,
                    argv,
                    result.stdout if isinstance(result.stdout, (str, bytes)) else None,
                    result.stderr if isinstance(result.stderr, (str, bytes)) else None,
                )
            return result
        finally:
            with suppress(Exception):
                child.close()
    if _can_exec_capture(capture_output=capture_output, input=input, extra=extra):
        return _run_hidden_exec_capture(
            args,
            text=text,
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=check,
        )
    P._refuse_soft_pipe_spawn("run_hidden")


def _run_hidden_exec_capture(
    args: Sequence[str],
    *,
    text: bool,
    timeout: float | None,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    check: bool,
) -> subprocess.CompletedProcess[Any]:
    from remedy.core.computer import host_binding

    resolved = resolve_argv0(args)
    token, now_ms = host_binding.issue_process_spawn_token(resolved)
    timeout_ms = 0 if timeout is None else int(max(0.0, float(timeout)) * 1000)
    captured = host_binding.process_exec_capture_authorized(
        resolved,
        str(cwd) if cwd else None,
        env,
        token=token,
        now_ms=now_ms,
        timeout_ms=timeout_ms,
    )
    if captured.timed_out:
        raise subprocess.TimeoutExpired(
            cmd=[str(a) for a in args],
            timeout=0.0 if timeout is None else float(timeout),
            output=(
                captured.stdout.decode("utf-8", "replace")
                if text
                else captured.stdout
            ),
            stderr=(
                captured.stderr.decode("utf-8", "replace")
                if text
                else captured.stderr
            ),
        )
    stdout: Any = captured.stdout
    stderr: Any = captured.stderr
    if text:
        stdout = captured.stdout.decode("utf-8", "replace")
        stderr = captured.stderr.decode("utf-8", "replace")
    result = subprocess.CompletedProcess(list(args), int(captured.exit_code), stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, list(args), result.stdout, result.stderr
        )
    return result


async def run_hidden_async(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = DEFAULT_RUN_TIMEOUT_S,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input: str | bytes | None = None,
    **extra: Any,
) -> subprocess.CompletedProcess[Any]:
    """Async wrapper around :func:`run_hidden`."""
    return await asyncio.to_thread(
        run_hidden,
        args,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        cwd=cwd,
        env=env,
        check=check,
        input=input,
        **extra,
    )


def popen_hidden(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    **extra: Any,
) -> PipedProcess:
    """Authorized piped spawn when stdio pipes are requested; else fail closed."""
    from remedy.execution import process as P

    P.require_process_host()
    want_text = bool(text or encoding or errors)
    if _stdio_is_pipe_request(stdout=stdout, stderr=stderr, stdin=stdin, extra=extra):
        return P.spawn_piped(args, cwd=cwd, env=env, text=want_text)
    _ = (encoding, errors)
    P._refuse_soft_pipe_spawn("popen_hidden")


async def create_hidden_subprocess_exec(
    program: str,
    *args: str,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    **extra: Any,
) -> asyncio.subprocess.Process:
    """Fail closed — no Zig async pipe-spawn. Prefer :func:`spawn_piped`."""
    from remedy.execution import process as P

    _ = (program, args, stdout, stderr, stdin, cwd, env, extra)
    P.require_process_host()
    P._refuse_soft_pipe_spawn("create_hidden_subprocess_exec")

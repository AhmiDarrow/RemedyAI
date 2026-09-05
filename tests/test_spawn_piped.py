"""Focused tests for Zig authorized interactive 3-pipe spawn."""

from __future__ import annotations

import json
import sys
import threading

import pytest

from remedy.core.computer import host_binding as H
from remedy.execution import process as P
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError


def _core_available() -> bool:
    try:
        H._lib()
        return True
    except (NativeRuntimeUnavailableError, OSError, AttributeError):
        return False


requires_core = pytest.mark.skipif(not _core_available(), reason="remedy_core not built")
host_platform = pytest.mark.skipif(
    sys.platform not in ("win32", "linux"),
    reason="authorized piped spawn is win32/linux",
)


@requires_core
@host_platform
def test_spawn_piped_json_rpc_round_trip() -> None:
    """Child reads one JSON line on stdin and writes one JSON line on stdout."""
    script = (
        "import json,sys;"
        "req=json.loads(sys.stdin.readline());"
        "sys.stdout.write(json.dumps({'ok': True, 'echo': req.get('ping')})+'\\n');"
        "sys.stdout.flush()"
    )
    child = P.spawn_piped([sys.executable, "-c", script], text=True)
    try:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(json.dumps({"ping": "piped-ok"}) + "\n")
        child.stdin.flush()
        line = child.stdout.readline()
        assert line, "child produced no stdout"
        reply = json.loads(line)
        assert reply.get("ok") is True
        assert reply.get("echo") == "piped-ok"
        code = child.wait(timeout=10)
        assert code == 0
    finally:
        child.close()


@requires_core
@host_platform
def test_spawn_piped_stderr_is_separate() -> None:
    script = (
        "import sys;"
        "sys.stderr.write('err-line\\n');"
        "sys.stderr.flush();"
        "sys.stdout.write('out-line\\n');"
        "sys.stdout.flush()"
    )
    child = P.spawn_piped([sys.executable, "-c", script], text=True)
    try:
        out_box: dict[str, str] = {}
        err_box: dict[str, str] = {}

        def _read_out() -> None:
            out_box["line"] = child.stdout.readline()  # type: ignore[union-attr]

        def _read_err() -> None:
            err_box["line"] = child.stderr.readline()  # type: ignore[union-attr]

        t_out = threading.Thread(target=_read_out, daemon=True)
        t_err = threading.Thread(target=_read_err, daemon=True)
        t_out.start()
        t_err.start()
        t_out.join(5)
        t_err.join(5)
        assert (out_box.get("line") or "").strip() == "out-line"
        assert (err_box.get("line") or "").strip() == "err-line"
    finally:
        child.close()

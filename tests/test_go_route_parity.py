"""Phase 4 route parity: hit Go remedy-runtime, not FastAPI TestClient.

Spawns ``remedy-runtime --smoke-fixture`` on an ephemeral loopback port when a
binary or ``go`` toolchain is available. Set ``REMEDY_ROUTE_PARITY_URL`` to
attach to an already-running Go server instead. Skips cleanly when neither is
possible; when the server is up, assertions fail closed (no soft 404 pass, no
KNOWN_GO_GAPS widening).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from remedy.interfaces.cli.cmd_runtime import resolve_remedy_runtime_command

ROOT = Path(__file__).resolve().parents[1]
GO_MOD = ROOT / "native" / "go"
_LISTEN_RE = re.compile(r"listening on http://(\S+)")
_TOKEN = "route-parity-token-16chars"
_READY_TIMEOUT_S = 90.0


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 8.0,
) -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            payload: object = None
            if raw:
                payload = json.loads(raw.decode("utf-8"))
            return int(resp.status), payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = raw.decode("utf-8", errors="replace")
        return int(exc.code), payload


def _ping_ok(base: str) -> bool:
    try:
        code, body = _http_json("GET", f"{base.rstrip('/')}/api/ping", timeout=2.0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return code == 200 and isinstance(body, dict) and body.get("status") == "ok"


def _build_worktree_runtime(build_dir: Path) -> list[str] | None:
    """Compile this checkout's remedy-runtime (avoids stale PATH binaries)."""
    go = shutil.which("go")
    if go is None or not (GO_MOD / "go.mod").is_file():
        return None
    out = build_dir / (
        "remedy-runtime.exe" if sys.platform == "win32" else "remedy-runtime"
    )
    built = subprocess.run(
        [go, "build", "-o", str(out), "./cmd/remedy-runtime"],
        cwd=str(GO_MOD),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if built.returncode != 0 or not out.is_file():
        return None
    return [str(out)]


def _binary_supports_smoke_fixture(argv: list[str]) -> bool:
    try:
        probe = subprocess.run(
            [*argv, "-h"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    help_text = (probe.stdout or "") + (probe.stderr or "")
    return "smoke-fixture" in help_text


def _runtime_argv(build_dir: Path) -> list[str] | None:
    built = _build_worktree_runtime(build_dir)
    if built is not None:
        return built
    cmd = resolve_remedy_runtime_command()
    if cmd is None:
        return None
    # `go run` fallback when build helper missed (should be rare).
    if len(cmd) >= 3 and cmd[1] == "run" and "remedy-runtime" in cmd[2]:
        return None
    if not _binary_supports_smoke_fixture(cmd):
        return None
    return list(cmd)


def _stop_proc(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


@pytest.fixture(scope="module")
def go_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Yield ``(base_url, bearer_token)`` for a live Go httpapi, or skip."""
    attach = os.environ.get("REMEDY_ROUTE_PARITY_URL", "").strip().rstrip("/")
    if attach:
        if not _ping_ok(attach):
            pytest.skip(f"REMEDY_ROUTE_PARITY_URL set but not reachable: {attach}")
        token = os.environ.get("REMEDY_API_KEY", "").strip() or _TOKEN
        yield attach, token
        return

    home = tmp_path_factory.mktemp("go-route-parity-home")
    argv = _runtime_argv(home)
    if argv is None:
        pytest.skip("remedy-runtime unavailable (no binary and go build failed/missing)")

    env = os.environ.copy()
    env["REMEDY_HOME"] = str(home)
    env["REMEDY_API_KEY"] = _TOKEN
    env["REMEDY_API_AUTH"] = "1"
    # Isolate from suite defaults that disable auth / point at other homes.
    env.pop("REMEDY_NATIVE_RUNTIME_BIN", None)

    cmd = [*argv, "--smoke-fixture", "--listen", "127.0.0.1:0"]
    cwd = str(GO_MOD) if GO_MOD.is_dir() else None
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    bound: dict[str, str | None] = {"addr": None}
    err_lines: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_lines.append(line.rstrip())
            m = _LISTEN_RE.search(line)
            if m and bound["addr"] is None:
                bound["addr"] = m.group(1)

    reader = threading.Thread(target=_drain, name="go-route-parity-stderr", daemon=True)
    reader.start()

    deadline = time.monotonic() + _READY_TIMEOUT_S
    base = ""
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = "\n".join(err_lines[-20:])
                pytest.fail(
                    f"remedy-runtime exited before bind (code={proc.returncode}):\n{tail}"
                )
            if bound["addr"]:
                base = f"http://{bound['addr']}"
                if _ping_ok(base):
                    break
            time.sleep(0.05)
        else:
            _stop_proc(proc)
            tail = "\n".join(err_lines[-20:])
            pytest.fail(f"remedy-runtime did not become ready within {_READY_TIMEOUT_S}s:\n{tail}")

        yield base, _TOKEN
    finally:
        _stop_proc(proc)
        reader.join(timeout=2)


def test_go_ping_reports_native_runtime(go_runtime: tuple[str, str]) -> None:
    base, _token = go_runtime
    code, body = _http_json("GET", f"{base}/api/ping")
    assert code == 200, body
    assert isinstance(body, dict)
    assert body.get("status") == "ok"
    assert body.get("version")
    native = body.get("native_runtime")
    assert isinstance(native, dict)
    assert native.get("ready") is True
    assert native.get("effective") == "native"


def test_go_status_core_shape(go_runtime: tuple[str, str]) -> None:
    base, token = go_runtime
    code, body = _http_json("GET", f"{base}/api/status", token=token)
    assert code == 200, body
    assert isinstance(body, dict)
    assert body.get("status") == "ok"
    assert body.get("version")
    assert "gateway" in body
    assert "chat_sessions_count" in body


def test_go_sessions_list_and_create(go_runtime: tuple[str, str]) -> None:
    base, token = go_runtime
    code, body = _http_json("GET", f"{base}/api/sessions", token=token)
    assert code == 200, body
    assert isinstance(body, dict)
    assert isinstance(body.get("sessions"), list)

    code, created = _http_json(
        "POST",
        f"{base}/api/sessions",
        token=token,
        body={"title": "route-parity"},
    )
    assert code == 200, created
    assert isinstance(created, dict)
    sid = created.get("id")
    assert isinstance(sid, str) and sid

    code, listed = _http_json("GET", f"{base}/api/sessions", token=token)
    assert code == 200, listed
    assert isinstance(listed, dict)
    ids = {s.get("id") for s in listed.get("sessions", []) if isinstance(s, dict)}
    assert sid in ids


def test_go_sessions_require_bearer(go_runtime: tuple[str, str]) -> None:
    base, _token = go_runtime
    code, body = _http_json("GET", f"{base}/api/sessions")
    assert code == 401, body
    assert isinstance(body, dict)
    assert body.get("error") == "Unauthorized"


def test_go_turn_active_public(go_runtime: tuple[str, str]) -> None:
    base, _token = go_runtime
    code, body = _http_json("GET", f"{base}/api/turn-active")
    assert code == 200, body
    assert isinstance(body, dict)
    assert body.get("status") == "ok"
    assert body.get("active") is False

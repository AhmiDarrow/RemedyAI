"""Disposable Remedy install for harness runs.

Every run gets its own ``REMEDY_HOME`` and project workspace under a temp root,
so a scenario that writes files, edits settings, or wipes memory can never reach
the owner's real ``~/.remedy``. The serve process is started with that home in
the environment and torn down at the end.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def venv_python() -> str:
    """Interpreter that has Remedy's deps - prefer the repo venv."""
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = REPO / ".venv" / rel
        if cand.is_file():
            return str(cand)
    return sys.executable


@dataclass
class Sandbox:
    """A throwaway Remedy home + workspace + serve process."""

    root: Path
    home: Path
    workspace: Path
    trace_dir: Path
    port: int
    proc: subprocess.Popen | None = None
    log_path: Path | None = None
    token: str = ""
    env: dict[str, str] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- lifecycle -------------------------------------------------------

    def write_config(
        self,
        *,
        provider: str,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        approval_mode: str = "auto",
        access_scope: str = "project",
        thinking_level: str = "low",
        extra: dict[str, object] | None = None,
    ) -> Path:
        """Write ``config.toml`` for this sandbox.

        ``approval_mode = "auto"`` lets in-project tools run unattended, which
        is what an unsupervised scenario needs; the path jail still applies, so
        an escape attempt is a real finding rather than a real deletion.
        """

        def tv(v: object) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (int, float)):
                return str(v)
            if isinstance(v, (list, tuple)):
                return "[" + ", ".join(tv(x) for x in v) + "]"
            return json.dumps(str(v))

        cfg: dict[str, object] = {
            "name": "Remedy",
            "home_dir": self.home.as_posix(),
            "project_path": self.workspace.as_posix(),
            "access_scope": access_scope,
            "approval_mode": approval_mode,
            "thinking_level": thinking_level,
            "llm_provider": provider,
            "llm_model": model,
            "memory_db_path": (self.home / "memory.db").as_posix(),
            "enabled_channels": ["cli"],
            "log_level": "INFO",
            "skills_dir": [],
            "mcp_servers": [],
            # Harness runs are offline and unattended: no web calls, no wizard.
            "web_tools_enabled": False,
            "allow_skill_creation": False,
        }
        if base_url:
            cfg["llm_base_url"] = base_url
        if api_key:
            cfg["llm_api_key"] = api_key
        cfg.update(extra or {})

        lines = [f"{k} = {tv(v)}" for k, v in cfg.items()]
        path = self.home / "config.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_rmb_json(
        self,
        *,
        port: int,
        ctx_size: int,
        model_path: str = "",
        host: str = "127.0.0.1",
    ) -> Path:
        """Declare the running llama-server so Remedy budgets against reality.

        ``resolve_local_window`` reads ``rmb.json`` first. Without this file the
        sandbox falls back to ``DEFAULT_CTX`` (8192) while the server may be
        serving 16k or more - Remedy then fits every request into half the
        window it actually has, and tool schemas are the first thing dropped.
        """
        state = {
            "enabled": True,
            "auto_start": False,
            "host": host,
            "port": int(port),
            "base_url": f"http://{host}:{port}/v1",
            "model_path": str(model_path),
            "ctx_size": int(ctx_size),
            "n_gpu_layers": -1,
            "use_jinja": True,
            "flash_attn": True,
            "profile": "agent",
            "autofit": False,
            # The harness owns the process; Remedy must not try to manage it.
            "user_stopped": False,
            "vision_suspended": True,
        }
        path = self.home / "rmb" / "rmb.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return path

    def start(self, *, timeout: float = 180.0, trace: bool = True) -> None:
        """Launch ``remedy serve`` against this sandbox and wait for readiness."""
        env = dict(os.environ)
        env["REMEDY_HOME"] = str(self.home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Unattended: no first-run wizard, no desktop sidecar, no auto-update.
        env["REMEDY_SKIP_SETUP"] = "1"
        env["REMEDY_DISABLE_AUTOUPDATE"] = "1"
        if trace:
            env["REMEDY_LLM_TRACE_DIR"] = str(self.trace_dir)
        else:
            env.pop("REMEDY_LLM_TRACE_DIR", None)
        self.env = env

        self.log_path = self.root / "serve.log"
        log = self.log_path.open("w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [
                venv_python(),
                "-m",
                "remedy.interfaces.cli",
                # Belt and braces: --home is what the instance lock and store
                # actually follow, so never rely on REMEDY_HOME alone here.
                "--home",
                str(self.home),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--skip-setup",
                "--no-computer-host",
            ],
            cwd=str(REPO),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"serve exited early (code {self.proc.returncode}). "
                    f"Log: {self.log_path}\n{self.tail_log()}"
                )
            if self._status_ok():
                self.token = self._resolve_token()
                return
            time.sleep(0.5)
        raise TimeoutError(
            f"serve did not become ready on {self.base} within {timeout:.0f}s. "
            f"Log: {self.log_path}\n{self.tail_log()}"
        )

    def _status_ok(self) -> bool:
        if not port_open(self.port):
            return False
        try:
            req = urllib.request.Request(
                f"{self.base}/api/status", headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 500
        except urllib.error.HTTPError as e:
            # 401/403 still proves the app is up and routing.
            return e.code in (401, 403)
        except Exception:
            return False

    def _resolve_token(self) -> str:
        sys.path.insert(0, str(REPO / "scripts"))
        from lib_local_token import resolve_local_api_token

        return resolve_local_api_token(home=self.home, base=self.base)

    def tail_log(self, lines: int = 40) -> str:
        if not self.log_path or not self.log_path.is_file():
            return ""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def stop(self) -> None:
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)

    def cleanup(self, *, keep: bool = False) -> None:
        self.stop()
        if keep:
            return
        shutil.rmtree(self.root, ignore_errors=True)


def make_sandbox(*, root: Path | str | None = None, port: int | None = None) -> Sandbox:
    """Create the directory layout for one harness run."""
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="remedy-rig-"))
    base.mkdir(parents=True, exist_ok=True)
    home = base / "home"
    workspace = base / "workspace"
    traces = base / "traces"
    for p in (home, workspace, traces):
        p.mkdir(parents=True, exist_ok=True)
    return Sandbox(
        root=base,
        home=home,
        workspace=workspace,
        trace_dir=traces,
        port=port or free_port(),
    )

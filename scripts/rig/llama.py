"""Stand up a llama-server for one GGUF so the harness can score it.

Binding on port 8787 is deliberate: ``is_rmb_provider`` treats that port as the
Remedy Muscle Bridge, which is what flips the agent into local-agent mode
(no streaming on tool rounds, thinking forced low, write-first tool filter,
48-tool slim, hard context fitting). Scoring a local model on any other port
would measure a configuration nobody ships.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RMB_PORT = 8787


def remedy_home() -> Path:
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def find_llama_server() -> Path | None:
    """Locate llama-server: Remedy's bundled runtimes first, then PATH."""
    home = remedy_home()
    candidates = [
        home / "rmb" / "runtime",
        home / "vision" / "runtime",
        home / "runtime",
    ]
    for root in candidates:
        for name in ("llama-server.exe", "llama-server"):
            p = root / name
            if p.is_file():
                return p
    for root in candidates:
        if root.is_dir():
            for p in root.rglob("llama-server*"):
                if p.is_file() and p.suffix in ("", ".exe"):
                    return p
    which = shutil.which("llama-server")
    return Path(which) if which else None


def has_cuda(server: Path | None = None) -> bool:
    """True when the runtime beside llama-server carries CUDA backends.

    A CPU-only build silently ignores ``--n-gpu-layers``, so an unnoticed
    CPU runtime turns a 20 tok/s model into a 2 tok/s one and invalidates
    every timing in the scorecard.
    """
    server = server or find_llama_server()
    if server is None:
        return False
    d = server.parent
    markers = ("ggml-cuda.dll", "libggml-cuda.so", "ggml-cuda.so")
    if any((d / m).is_file() for m in markers):
        return True
    return any(d.glob("*cuda*"))


@dataclass
class LlamaServer:
    """A managed llama-server process."""

    model: Path
    server: Path
    port: int = RMB_PORT
    ctx: int = 16384
    ngl: int = 999
    threads: int = 0
    # DRY sampling breaks degenerate repetition. A model that loops inside a
    # forced tool-call grammar burns the whole n_predict budget (5k+ tokens,
    # ~75s at 70 tok/s) on one step and stalls the run. 0.0 disables it.
    dry_multiplier: float = 0.0
    # Keep MoE experts on the CPU and everything else on the GPU. Only the
    # active experts are read per token, so a 3B-active model stays fast
    # while its full weights live in system RAM - the only way a 35B class
    # model runs 'mostly on GPU' inside 12 GB.
    n_cpu_moe: int = 0
    cpu_moe_all: bool = False
    extra: tuple[str, ...] = ()
    proc: subprocess.Popen | None = None
    log_path: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self, *, timeout: float = 600.0, log_dir: Path | None = None) -> None:
        cmd = [
            str(self.server),
            "--model",
            str(self.model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--ctx-size",
            str(self.ctx),
            "--n-gpu-layers",
            str(self.ngl),
            "--jinja",  # chat template + native tool_call parsing
            "--flash-attn",
            "auto",
            "--cache-reuse",
            "256",
            # One slot, or llama-server allocates n_slots x ctx and reports the
            # total. Remedy budgets n_predict from the window it is told about,
            # so a 4-slot server turned --ctx 16384 into a 10922-token
            # completion budget - minutes of generation for a single tool step.
            "--parallel",
            "1",
        ]
        if self.threads:
            cmd += ["--threads", str(self.threads)]
        if self.cpu_moe_all:
            cmd.append("--cpu-moe")
        elif self.n_cpu_moe > 0:
            cmd += ["--n-cpu-moe", str(self.n_cpu_moe)]
        if self.dry_multiplier > 0:
            cmd += [
                "--dry-multiplier",
                str(self.dry_multiplier),
                "--dry-base",
                "1.75",
                "--dry-allowed-length",
                "2",
            ]
        cmd += list(self.extra)

        log_dir = log_dir or Path(os.environ.get("TEMP", ".")) / "rig-llama"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"llama-{self.model.stem[:40]}-{self.port}.log"
        log = self.log_path.open("w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.server.parent)
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited ({self.proc.returncode}). "
                    f"Log: {self.log_path}\n{self.tail()}"
                )
            if self.healthy():
                return
            time.sleep(1.0)
        self.stop()
        raise TimeoutError(
            f"llama-server not ready in {timeout:.0f}s. Log: {self.log_path}\n{self.tail()}"
        )

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/health", timeout=3
            ) as r:
                return r.status == 200
        except Exception:
            return False

    def loaded_model_id(self) -> str:
        """Model id llama-server reports - RMB uses the GGUF stem as the id."""
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/models")
            with urllib.request.urlopen(req, timeout=5) as r:
                import json

                data = json.loads(r.read().decode("utf-8"))
            items = data.get("data") or []
            if items:
                return str(items[0].get("id") or self.model.stem)
        except Exception:
            pass
        return self.model.stem

    def tail(self, lines: int = 30) -> str:
        if not self.log_path or not self.log_path.is_file():
            return ""
        try:
            return "\n".join(
                self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[
                    -lines:
                ]
            )
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def launch(
    model_path: str | Path,
    *,
    port: int = RMB_PORT,
    ctx: int = 16384,
    ngl: int = 999,
    threads: int = 0,
    dry_multiplier: float = 0.0,
    n_cpu_moe: int = 0,
    cpu_moe_all: bool = False,
    extra: tuple[str, ...] = (),
    server: str | Path | None = None,
) -> LlamaServer:
    model = Path(model_path).expanduser()
    if not model.is_file():
        raise SystemExit(f"GGUF not found: {model}")
    binary = Path(server).expanduser() if server else find_llama_server()
    if binary is None:
        raise SystemExit(
            "llama-server not found. Install Remedy's local runtime, or pass --llama-server."
        )
    srv = LlamaServer(
        model=model,
        server=binary,
        port=port,
        ctx=ctx,
        ngl=ngl,
        threads=threads,
        dry_multiplier=dry_multiplier,
        n_cpu_moe=n_cpu_moe,
        cpu_moe_all=cpu_moe_all,
        extra=extra,
    )
    srv.start()
    return srv

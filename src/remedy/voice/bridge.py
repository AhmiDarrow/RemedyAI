"""Sidecar-side client for :mod:`remedy.voice.worker`.

One long-lived worker process per sidecar, started on first use, restarted
if it dies. Calls are serialised (the engines are not thread-safe and one
model at a time is what a PC can do anyway). Every failure comes back as an
exception with plain words; the caller decides what the owner sees.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

from remedy.voice import runtime as rt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0


class WorkerError(RuntimeError):
    """The worker answered with an error, or could not answer at all."""


class VoiceBridge:
    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = str(home_dir) if home_dir else None
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._stderr_thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def available(self) -> bool:
        return rt.runtime_ready(self.home_dir)

    def _spawn(self) -> subprocess.Popen[str]:
        py = rt.python_path(self.home_dir)
        if not py.is_file():
            raise WorkerError("Remedy's voice runtime is not set up yet.")
        env = rt.child_env(self.home_dir, with_source=True)
        env["REMEDY_VOICE_WORKER"] = "1"
        from remedy.execution.process import hidden_subprocess_kwargs

        proc = subprocess.Popen(
            [str(py), "-m", "remedy.voice.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **hidden_subprocess_kwargs(),
        )
        t = threading.Thread(target=self._pump_stderr, args=(proc,), daemon=True)
        t.start()
        self._stderr_thread = t
        logger.info("voice worker started (pid %s) with %s", proc.pid, py)
        return proc

    @staticmethod
    def _pump_stderr(proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                # Engines chatter (deprecations, library notices); only the
                # worker's own warnings are worth the owner's log.
                if "remedy.voice" in line or "Traceback" in line or "Error" in line:
                    logger.warning("voice worker: %s", line)
                else:
                    logger.debug("voice worker: %s", line)
        except Exception:
            pass

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        with self._lock:
            p = self._proc
            self._proc = None
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
            p.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                p.kill()

    # -- calls ---------------------------------------------------------------

    def call(self, op: str, timeout: float = _DEFAULT_TIMEOUT, **args: Any) -> Any:
        with self._lock:
            if not self._alive():
                self._proc = self._spawn()
            proc = self._proc
            assert proc is not None and proc.stdin and proc.stdout
            self._next_id += 1
            rid = self._next_id
            if self.home_dir and "home_dir" not in args:
                args["home_dir"] = self.home_dir
            req = json.dumps({"id": rid, "op": op, "args": args}, ensure_ascii=True)
            try:
                proc.stdin.write(req + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._proc = None
                raise WorkerError(f"voice worker went away: {exc}") from exc
            reply = self._read_reply(proc, timeout)
            if reply.get("id") not in (rid, None):
                self._proc = None
                raise WorkerError("voice worker answered out of order")
            if not reply.get("ok"):
                raise WorkerError(str(reply.get("error") or "voice worker failed"))
            return reply.get("result")

    def _read_reply(self, proc: subprocess.Popen[str], timeout: float) -> dict[str, Any]:
        assert proc.stdout is not None
        box: dict[str, Any] = {}

        def reader() -> None:
            try:
                box["line"] = proc.stdout.readline()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                box["exc"] = exc

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # A hung engine: kill and let the next call respawn.
            try:
                proc.kill()
            finally:
                self._proc = None
            raise WorkerError("voice worker did not answer in time")
        if "exc" in box:
            self._proc = None
            raise WorkerError(f"voice worker read failed: {box['exc']}")
        line = (box.get("line") or "").strip()
        if not line:
            self._proc = None
            raise WorkerError("voice worker exited")
        try:
            out = json.loads(line)
        except json.JSONDecodeError as exc:
            self._proc = None
            raise WorkerError(f"voice worker spoke garbage: {line[:80]}") from exc
        return out if isinstance(out, dict) else {"ok": False, "error": "bad reply"}

    # -- typed helpers -------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        out = self.call("probe", timeout=60)
        return dict(out or {})

    def synthesize(
        self,
        text: str,
        *,
        gender: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
    ) -> tuple[bytes, int] | None:
        out = self.call(
            "synthesize", timeout=300, text=text, gender=gender, voice=voice, speed=speed
        )
        if not out:
            return None
        return base64.b64decode(out["wav_b64"]), int(out["sample_rate"])

    def transcribe(self, path: Path | str, *, language: str | None = None) -> dict[str, Any] | None:
        out = self.call("transcribe", timeout=600, path=str(path), language=language)
        return out if isinstance(out, dict) else None

    def warm_stt(self) -> dict[str, Any]:
        return self.call("warm_stt", timeout=3600) or {}

    def warm_hq(self) -> dict[str, Any]:
        return self.call("warm_hq", timeout=7200) or {}

    def hq_state(self) -> dict[str, Any] | None:
        out = self.call("hq_state", timeout=30) or {}
        st = out.get("state")
        return st if isinstance(st, dict) else None

    def turn_score(self, pcm: bytes, model_path: str) -> float:
        out = self.call(
            "turn_score",
            timeout=10,
            pcm_b64=base64.b64encode(pcm).decode("ascii"),
            model_path=model_path,
        )
        return float((out or {}).get("score", 0.0))


_bridges: dict[str, VoiceBridge] = {}
_bridges_lock = threading.Lock()


def get_bridge(home_dir: Path | str | None = None) -> VoiceBridge:
    key = str(home_dir or "")
    with _bridges_lock:
        b = _bridges.get(key)
        if b is None:
            b = VoiceBridge(home_dir)
            _bridges[key] = b
        return b


def stop_all() -> None:
    with _bridges_lock:
        items = list(_bridges.values())
        _bridges.clear()
    for b in items:
        b.stop()

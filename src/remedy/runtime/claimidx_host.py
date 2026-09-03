"""Managed Claimidx service installed on first run.

Claimidx is intentionally not part of the Remedy installer.  Remedy creates an
isolated virtual environment below ``~/.remedy/claimidx``, downloads the pinned
wheel from PyPI, verifies its sha256, seeds a private local index, and starts a
loopback-only service.  A failed or offline install never blocks Remedy.

The managed service does not inherit an owner's global Claimidx configuration
or credentials and public sharing stays disabled.  This prevents a local
Remedy install from accidentally publishing claims or serving another profile's
database.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from remedy.home import default_home

logger = logging.getLogger(__name__)

CLAIMIDX_VERSION = "0.6.1"
CLAIMIDX_WHEEL = f"claimidx-{CLAIMIDX_VERSION}-py3-none-any.whl"
CLAIMIDX_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/14/3e/"
    "e63c537c294fe06ec79677af08f726a039362c99190095560bc85c4733d3/"
    + CLAIMIDX_WHEEL
)
CLAIMIDX_WHEEL_SHA256 = (
    "50ffe85d2e350bb4b9b39ebbc0ed8b32a33ee2f4da9572d2673ee244ac3af7b3"
)
CLAIMIDX_WHEEL_MAX_BYTES = 2_000_000
CLAIMIDX_HOST = "127.0.0.1"
# Dedicated port: never commandeer an owner's separately managed Claimidx home
# on its standard 7340 listener.
CLAIMIDX_PORT = 17340

_proc: subprocess.Popen[Any] | None = None
_lock = threading.Lock()
_ensure_started = False
_atexit_registered = False


def _root(home_dir: str | Path | None = None) -> Path:
    if home_dir is not None:
        base = Path(home_dir).expanduser()
    else:
        raw = (os.environ.get("REMEDY_HOME") or "").strip()
        base = Path(raw).expanduser() if raw else default_home()
    return base / "claimidx"


def _runtime_dir(home_dir: str | Path | None = None) -> Path:
    return _root(home_dir) / f"runtime-{CLAIMIDX_VERSION}"


def _venv_python(home_dir: str | Path | None = None) -> Path:
    root = _runtime_dir(home_dir)
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _marker_path(home_dir: str | Path | None = None) -> Path:
    return _runtime_dir(home_dir) / "remedy-claimidx.json"


def _read_marker(home_dir: str | Path | None = None) -> dict[str, Any]:
    try:
        value = json.loads(_marker_path(home_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _installed(home_dir: str | Path | None = None) -> bool:
    marker = _read_marker(home_dir)
    return bool(
        _venv_python(home_dir).is_file()
        and marker.get("ok")
        and marker.get("version") == CLAIMIDX_VERSION
        and marker.get("wheel_sha256") == CLAIMIDX_WHEEL_SHA256
    )


def _skip_network() -> bool:
    if os.environ.get("REMEDY_ENSURE_ASSETS") == "1":
        return False
    if os.environ.get("REMEDY_CLAIMIDX_DISABLE") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _service_token(home_dir: str | Path | None = None) -> str:
    """Return the private write token for Remedy's loopback Claimidx home."""
    path = _root(home_dir) / "service-token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token.startswith("spt_") and len(token) >= 24:
        return token
    token = "spt_" + secrets.token_urlsafe(32)
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(path, token + "\n", mode=0o600)
    return token


def _clean_env(home_dir: str | Path | None = None) -> dict[str, str]:
    """Child environment isolated from global Claimidx and PyInstaller state."""
    from remedy.voice.runtime import child_env

    env = child_env(home_dir, with_source=False)
    for key in tuple(env):
        if key.startswith("CLAIMIDX_"):
            env.pop(key, None)
    root = _root(home_dir)
    env.update(
        {
            "CLAIMIDX_CONFIG": str(root / "config.json"),
            "CLAIMIDX_DB": str(root / "index.sqlite"),
            "CLAIMIDX_OWNER": "did:claimidx:remedy",
            "CLAIMIDX_AGENT": "remedy",
            "CLAIMIDX_SHARE": "0",
            # Protect every mutating endpoint even though the server only
            # binds loopback. Read-only prior art remains locally available.
            "CLAIMIDX_HOME_TOKEN": _service_token(home_dir),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return env


def _base_python(home_dir: str | Path | None = None) -> Path:
    from remedy.core.runtime_identity import is_frozen_install

    if not is_frozen_install():
        current = Path(sys.executable)
        if current.is_file() and current.stem.lower().startswith("python"):
            return current
    # Packaged Remedy has no importable Python.  Reuse the same pinned,
    # checksum-verified CPython base downloaded for first-run voice support.
    from remedy.voice.runtime import install_runtime

    return install_runtime(home_dir)


def _download_wheel(dest: Path, *, timeout: float = 60.0) -> None:
    req = Request(CLAIMIDX_WHEEL_URL, headers={"User-Agent": "RemedyAI-Claimidx/1.0"})
    digest = hashlib.sha256()
    size = 0
    with urlopen(req, timeout=timeout) as response, dest.open("wb") as out:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > CLAIMIDX_WHEEL_MAX_BYTES:
                raise RuntimeError("Claimidx wheel was larger than the pinned limit")
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest().lower() != CLAIMIDX_WHEEL_SHA256:
        raise RuntimeError("Claimidx wheel sha256 did not match the pinned release")


def _run(args: list[str], env: dict[str, str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    from remedy.execution.process import run_hidden

    return run_hidden(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def ensure_installed(
    home_dir: str | Path | None = None, *, timeout: float = 360.0
) -> dict[str, Any]:
    """Install the pinned Claimidx release into an isolated environment."""
    if _installed(home_dir):
        return {
            "ok": True,
            "path": str(_venv_python(home_dir)),
            "downloaded": False,
            "version": CLAIMIDX_VERSION,
        }
    if _skip_network():
        return {"ok": False, "skipped": True, "reason": "network_disabled"}

    with _lock:
        if _installed(home_dir):
            return {
                "ok": True,
                "path": str(_venv_python(home_dir)),
                "downloaded": False,
                "version": CLAIMIDX_VERSION,
            }
        runtime = _runtime_dir(home_dir)
        try:
            root = _root(home_dir)
            root.mkdir(parents=True, exist_ok=True)
            # The marker is written last. A directory without it is an
            # interrupted install and is safe to replace; never touch another
            # version's runtime.
            if runtime.exists():
                shutil.rmtree(runtime)
            env = _clean_env(home_dir)
            base = _base_python(home_dir)
            created = _run(
                [str(base), "-m", "venv", str(runtime)], env, timeout=120.0
            )
            if created.returncode != 0:
                raise RuntimeError(
                    "could not create Claimidx runtime: "
                    + (created.stderr or created.stdout or f"exit {created.returncode}")[-500:]
                )
            py = _venv_python(home_dir)
            with tempfile.TemporaryDirectory(prefix="remedy-claimidx-") as tmp:
                wheel = Path(tmp) / CLAIMIDX_WHEEL
                _download_wheel(wheel)
                requirement = f"claimidx[server] @ {wheel.resolve().as_uri()}"
                installed = _run(
                    [
                        str(py),
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        requirement,
                    ],
                    env,
                    timeout=timeout,
                )
            if installed.returncode != 0:
                raise RuntimeError(
                    "could not install Claimidx: "
                    + (installed.stderr or installed.stdout or f"exit {installed.returncode}")[-500:]
                )
            probe = _run(
                [
                    str(py),
                    "-c",
                    "import claimidx, fastapi, uvicorn; print(claimidx.__version__)",
                ],
                env,
                timeout=30.0,
            )
            if probe.returncode != 0 or probe.stdout.strip() != CLAIMIDX_VERSION:
                raise RuntimeError("Claimidx runtime verification failed")
            from remedy.core.atomic_json import write_json_atomic

            write_json_atomic(
                _marker_path(home_dir),
                {
                    "ok": True,
                    "version": CLAIMIDX_VERSION,
                    "wheel_sha256": CLAIMIDX_WHEEL_SHA256,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            return {
                "ok": True,
                "path": str(py),
                "downloaded": True,
                "version": CLAIMIDX_VERSION,
            }
        except Exception as exc:
            # Even best-effort cleanup can be refused by endpoint protection
            # or a concurrent scanner on Windows. Preserve the original
            # install failure and let the next launch retry the exact runtime.
            with suppress(Exception):
                shutil.rmtree(runtime, ignore_errors=True)
            logger.warning("Claimidx first-run install failed: %s", exc)
            return {"ok": False, "error": str(exc)}


def _setup(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Create a private config and seed the managed index without network I/O."""
    root = _root(home_dir)
    root.mkdir(parents=True, exist_ok=True)
    from remedy.core.atomic_json import write_json_atomic

    write_json_atomic(
        root / "config.json",
        {
            "owner": "did:claimidx:remedy",
            "agent": "remedy",
            "share": False,
        },
    )
    seed_marker = root / "seed.json"
    try:
        seed_state = json.loads(seed_marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        seed_state = {}
    if isinstance(seed_state, dict) and seed_state.get("version") == CLAIMIDX_VERSION:
        return {"ok": True, "seeded": False}
    py = _venv_python(home_dir)
    result = _run(
        [
            str(py),
            "-m",
            "claimidx.cli",
            "--db",
            str(root / "index.sqlite"),
            "seed",
        ],
        _clean_env(home_dir),
        timeout=120.0,
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "error": "Claimidx seed failed: "
            + (result.stderr or result.stdout or f"exit {result.returncode}")[-500:],
        }
    write_json_atomic(
        seed_marker,
        {
            "version": CLAIMIDX_VERSION,
            "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return {"ok": True, "seeded": True}


def base_url() -> str:
    return f"http://{CLAIMIDX_HOST}:{CLAIMIDX_PORT}"


def is_healthy(*, timeout: float = 0.6) -> bool:
    """Only adopt Remedy's managed Claimidx identity on the dedicated port."""
    try:
        req = Request(
            f"{base_url()}/api/whoami",
            headers={"User-Agent": "RemedyAI-Claimidx-health/1.0"},
        )
        with urlopen(req, timeout=timeout) as response:
            if int(getattr(response, "status", 200) or 200) != 200:
                return False
            payload = json.loads(response.read(64 * 1024))
        operator = payload.get("operator") if isinstance(payload, dict) else None
        return bool(
            isinstance(payload, dict)
            and payload.get("product") == "Claimidx"
            and isinstance(operator, dict)
            and operator.get("did") == "did:claimidx:remedy"
            and operator.get("agent") == "remedy"
        )
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False


def _register_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(stop)
        _atexit_registered = True


def start(
    home_dir: str | Path | None = None, *, wait_s: float = 15.0
) -> dict[str, Any]:
    """Install, seed, and start Claimidx on loopback without blocking Remedy."""
    global _proc
    installed = ensure_installed(home_dir)
    if not installed.get("ok"):
        return installed
    setup = _setup(home_dir)
    if not setup.get("ok"):
        return setup
    if is_healthy():
        return {"ok": True, "already": True, "url": base_url()}
    py = _venv_python(home_dir)
    root = _root(home_dir)
    cmd = [
        str(py),
        "-m",
        "claimidx.cli",
        "--db",
        str(root / "index.sqlite"),
        "serve",
        "--host",
        CLAIMIDX_HOST,
        "--port",
        str(CLAIMIDX_PORT),
    ]
    from remedy.execution.process import hidden_subprocess_kwargs

    try:
        with _lock:
            if is_healthy():
                return {"ok": True, "already": True, "url": base_url()}
            _proc = subprocess.Popen(
                cmd,
                cwd=str(root),
                env=_clean_env(home_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **hidden_subprocess_kwargs(),
            )
        _register_atexit()
    except OSError as exc:
        return {"ok": False, "error": f"failed to start Claimidx: {exc}"}

    deadline = time.monotonic() + max(2.0, wait_s)
    while time.monotonic() < deadline:
        if is_healthy():
            logger.info("Claimidx %s listening on %s", CLAIMIDX_VERSION, base_url())
            return {
                "ok": True,
                "url": base_url(),
                "pid": _proc.pid if _proc else None,
                "version": CLAIMIDX_VERSION,
            }
        proc = _proc
        if proc is not None and proc.poll() is not None:
            return {
                "ok": False,
                "error": f"Claimidx exited early (code {proc.returncode})",
            }
        time.sleep(0.2)
    stop()
    return {"ok": False, "error": "Claimidx did not become healthy in time"}


def stop() -> None:
    global _proc
    with _lock:
        proc = _proc
        _proc = None
    if proc is None or proc.poll() is not None:
        return
    with suppress(Exception):
        proc.terminate()
        proc.wait(timeout=3)
        return
    with suppress(Exception):
        proc.kill()


def schedule_ensure(home_dir: str | Path | None = None) -> None:
    """Start exactly one non-blocking first-run ensure per Remedy process."""
    global _ensure_started
    if _skip_network():
        return
    with _lock:
        if _ensure_started:
            return
        _ensure_started = True

    def _worker() -> None:
        try:
            result = start(home_dir)
            if result.get("ok"):
                logger.info("Claimidx ready: %s", result.get("url") or "ok")
            elif not result.get("skipped"):
                logger.info("Claimidx first-run ensure: %s", result.get("error") or result)
        except Exception:
            logger.exception("Claimidx first-run ensure failed")

    threading.Thread(target=_worker, name="remedy-claimidx", daemon=True).start()

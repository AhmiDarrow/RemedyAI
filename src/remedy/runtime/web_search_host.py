"""Managed local OpenSERP — first-run download, loopback-only search API.

OpenSERP is MIT-licensed. Remedy downloads the pinned GitHub release into
``~/.remedy/bin`` (same pattern as ripgrep / llama-server), binds it to
127.0.0.1, and talks to it over a dedicated port. The process is never
exposed on the LAN. If the binary is missing or not yet healthy, web_search
falls through to the in-process DuckDuckGo HTML path.

Not started inside pytest unless ``REMEDY_ENSURE_ASSETS=1``.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
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

OPENSERP_VERSION = "0.8.12"
OPENSERP_TAG = f"v{OPENSERP_VERSION}"
OPENSERP_BASE_URL = (
    f"https://github.com/karust/openserp/releases/download/{OPENSERP_TAG}"
)
# Dedicated port so a leftover `openserp serve` on 7000 is not ours to commandeer.
OPENSERP_PORT = 17410
OPENSERP_HOST = "127.0.0.1"

# sha256 of the official .tgz assets (GitHub release digest).
_ASSETS: dict[str, str] = {
    "openserp-windows-amd64-0.8.12.tgz": (
        "9b93dfdb747641fd8effbbd88fc852d547a0624bf6f391bb65d0d378416e227b"
    ),
    "openserp-windows-arm64-0.8.12.tgz": (
        "0c15b98fa5f558b53daf83226c1e856dc9f020c4b8c79297d228368bfe6953f9"
    ),
    "openserp-linux-amd64-0.8.12.tgz": (
        "9d1974d885713686d7825cf27c219822bb79445ce30e25ef3f1608652fec781b"
    ),
    "openserp-linux-arm64-0.8.12.tgz": (
        "ff2b2ebffd771cc72729bec950b031d9bcf68373b7b873e6b7b87211d3bcab62"
    ),
}

_proc: subprocess.Popen[Any] | None = None
_lock = threading.Lock()
_ensure_started = False
_atexit_registered = False


def _exe_name() -> str:
    return "openserp.exe" if sys.platform == "win32" else "openserp"


def _home_bin(home_dir: str | Path | None = None) -> Path:
    if home_dir is not None:
        base = Path(home_dir).expanduser()
    else:
        env = (os.environ.get("REMEDY_HOME") or "").strip()
        base = Path(env).expanduser() if env else default_home()
    return base / "bin"


def base_url() -> str:
    return f"http://{OPENSERP_HOST}:{OPENSERP_PORT}"


def _skip_network() -> bool:
    if os.environ.get("REMEDY_ENSURE_ASSETS") == "1":
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _platform_asset() -> tuple[str, str] | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    is_x64 = machine in ("x86_64", "amd64", "x64")
    if system == "windows":
        name = (
            f"openserp-windows-arm64-{OPENSERP_VERSION}.tgz"
            if is_arm
            else f"openserp-windows-amd64-{OPENSERP_VERSION}.tgz"
            if is_x64
            else ""
        )
    elif system == "linux":
        name = (
            f"openserp-linux-arm64-{OPENSERP_VERSION}.tgz"
            if is_arm
            else f"openserp-linux-amd64-{OPENSERP_VERSION}.tgz"
            if is_x64
            else ""
        )
    else:
        return None
    digest = _ASSETS.get(name)
    if not name or not digest:
        return None
    return name, digest


def find_binary(home_dir: str | Path | None = None) -> Path | None:
    cand = _home_bin(home_dir) / _exe_name()
    try:
        if cand.is_file():
            return cand.resolve()
    except OSError:
        return None
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_binary(archive: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe = _exe_name()
    found: Path | None = None
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            base = Path(member.name).name
            if base in ("openserp", "openserp.exe") and member.isfile():
                target = dest_dir / exe
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                with extracted, target.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
                found = target
                break
    if found is None:
        raise FileNotFoundError(f"openserp binary not found inside {archive.name}")
    with suppress(OSError):
        found.chmod(found.stat().st_mode | 0o111)
    return found


def ensure_binary(
    home_dir: str | Path | None = None, *, timeout: float = 120.0
) -> dict[str, Any]:
    """Download the pinned OpenSERP release if the binary is not on disk."""
    existing = find_binary(home_dir)
    if existing is not None:
        return {"ok": True, "path": str(existing), "downloaded": False}
    if _skip_network():
        return {"ok": False, "skipped": True, "error": "pytest"}
    asset = _platform_asset()
    if asset is None:
        return {
            "ok": False,
            "error": f"no OpenSERP build for {platform.system()} {platform.machine()}",
        }
    name, expected = asset
    dest_dir = _home_bin(home_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"{OPENSERP_BASE_URL}/{name}"
    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-OpenSERP-ensure/1.0"})
        with tempfile.TemporaryDirectory(prefix="remedy-openserp-") as tmp:
            tmp_path = Path(tmp) / name
            with urlopen(req, timeout=timeout) as resp, tmp_path.open("wb") as out:
                shutil.copyfileobj(resp, out)
            digest = _sha256_file(tmp_path)
            if digest.lower() != expected.lower():
                return {
                    "ok": False,
                    "error": (
                        f"checksum mismatch for {name}: got {digest}, expected {expected}"
                    ),
                }
            binary = _extract_binary(tmp_path, dest_dir)
        logger.info("OpenSERP installed %s", binary)
        return {"ok": True, "path": str(binary.resolve()), "downloaded": True}
    except Exception as exc:
        logger.warning("OpenSERP download failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def is_healthy(*, timeout: float = 0.6) -> bool:
    try:
        req = Request(
            f"{base_url()}/health",
            headers={"User-Agent": "RemedyAI-OpenSERP-health/1.0"},
        )
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 500
    except (OSError, URLError, TimeoutError, ValueError):
        return False


def _register_atexit() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(stop)
    _atexit_registered = True


def start(
    home_dir: str | Path | None = None, *, wait_s: float = 12.0
) -> dict[str, Any]:
    """Spawn OpenSERP on loopback if it is not already answering."""
    global _proc
    if is_healthy():
        return {"ok": True, "already": True, "url": base_url()}
    installed = ensure_binary(home_dir)
    if not installed.get("ok"):
        return installed
    binary = Path(str(installed["path"]))
    cmd = [
        str(binary),
        "serve",
        "-a",
        OPENSERP_HOST,
        "-p",
        str(OPENSERP_PORT),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with _lock:
            if is_healthy():
                return {"ok": True, "already": True, "url": base_url()}
            _proc = subprocess.Popen(
                cmd,
                cwd=str(binary.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        _register_atexit()
    except OSError as exc:
        return {"ok": False, "error": f"failed to start OpenSERP: {exc}"}
    deadline = time.time() + max(2.0, wait_s)
    while time.time() < deadline:
        if is_healthy():
            logger.info("OpenSERP listening on %s", base_url())
            return {"ok": True, "url": base_url(), "pid": _proc.pid if _proc else None}
        proc = _proc
        if proc is not None and proc.poll() is not None:
            return {
                "ok": False,
                "error": f"OpenSERP exited early (code {proc.returncode})",
            }
        time.sleep(0.2)
    return {"ok": False, "error": "OpenSERP did not become healthy in time"}


def stop() -> None:
    global _proc
    with _lock:
        proc = _proc
        _proc = None
    if proc is None:
        return
    if proc.poll() is not None:
        return
    with suppress(Exception):
        proc.terminate()
        proc.wait(timeout=3)
        return
    with suppress(Exception):
        proc.kill()


def ensure_web_search_host(
    home_dir: str | Path | None = None,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """First-run: download + start. Safe to call from the API lifespan thread."""
    if not enabled:
        return {"ok": False, "skipped": True, "reason": "web_tools_disabled"}
    if _skip_network():
        return {"ok": False, "skipped": True, "reason": "pytest"}
    return start(home_dir)


def schedule_ensure(
    home_dir: str | Path | None = None, *, enabled: bool = True
) -> None:
    """Kick off download/start once per process without blocking chat."""
    global _ensure_started
    if not enabled or _skip_network():
        return
    with _lock:
        if _ensure_started:
            return
        _ensure_started = True

    def _worker() -> None:
        try:
            result = ensure_web_search_host(home_dir, enabled=True)
            if result.get("ok"):
                logger.info("web search host ready: %s", result.get("url") or "ok")
            elif not result.get("skipped"):
                logger.info("web search host: %s", result.get("error") or result)
        except Exception:
            logger.exception("web search host ensure failed")

    threading.Thread(target=_worker, name="remedy-openserp", daemon=True).start()

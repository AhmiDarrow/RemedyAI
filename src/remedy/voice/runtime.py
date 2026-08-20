"""A Python of her own for the heavy voice extras.

The installed Desktop runs a frozen sidecar: no ``pip``, and torch can never
fit in an installer. So the voice engines (Kokoro, whisper, smart-turn,
Chatterbox) live in a **managed runtime** under ``~/.remedy/voice/runtime/``:
a pinned python-build-standalone CPython that the sidecar downloads once,
verifies by sha256, and pip-installs the packs into. Inference runs in that
Python through :mod:`remedy.voice.worker`, driven by
:mod:`remedy.voice.bridge`.

This is the same shape the vision stack uses for llama-server: a pinned,
verified runtime in the owner's home, never in the installer.

Dev (source tree / editable install) keeps importing the engines in-process;
``use_managed_runtime()`` decides.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PBS_TAG = "20260814"
_PBS_PY = "3.12.14"
_PBS_BASE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
)


@dataclass(frozen=True, slots=True)
class RuntimePin:
    """One pinned python-build-standalone archive."""

    triple: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return f"cpython-{_PBS_PY}+{_PBS_TAG}-{self.triple}-install_only_stripped.tar.gz"

    @property
    def url(self) -> str:
        return f"{_PBS_BASE}{_PBS_TAG}/{self.filename.replace('+', '%2B')}"


# sha256 from the upstream SHA256SUMS asset of the same release.
_PINS: dict[tuple[str, str], RuntimePin] = {
    ("Windows", "AMD64"): RuntimePin(
        "x86_64-pc-windows-msvc",
        "89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647",
        21_976_599,
    ),
    ("Linux", "x86_64"): RuntimePin(
        "x86_64-unknown-linux-gnu",
        "5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc",
        34_143_739,
    ),
    ("Linux", "aarch64"): RuntimePin(
        "aarch64-unknown-linux-gnu",
        "2d8e17dfd732102cfeb18e0e1fa6769b24caa034e159981129590fe409c7157a",
        29_217_771,
    ),
}

RUNTIME_DIRNAME = "runtime"
_MARKER = "runtime.json"
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Where / whether
# ---------------------------------------------------------------------------


def _voice_home(home_dir: Path | str | None) -> Path:
    from remedy.voice.service import voice_home

    return voice_home(home_dir)


def runtime_dir(home_dir: Path | str | None = None) -> Path:
    return _voice_home(home_dir) / RUNTIME_DIRNAME


def pin_for_this_machine() -> RuntimePin | None:
    key = (platform.system(), platform.machine())
    if key == ("Linux", "arm64"):
        key = ("Linux", "aarch64")
    return _PINS.get(key)


def inside_worker() -> bool:
    """True in the managed Python running :mod:`remedy.voice.worker`."""
    return os.environ.get("REMEDY_VOICE_WORKER") == "1"


def use_managed_runtime() -> bool:
    """Should this process reach the engines through the managed runtime?

    Frozen sidecar: always (it cannot pip). Dev: only when
    ``REMEDY_VOICE_MANAGED=1`` asks for it (tests, or reproducing the
    Desktop path from a checkout). The worker itself never does — it *is*
    the runtime.
    """
    if inside_worker():
        return False
    if os.environ.get("REMEDY_VOICE_MANAGED") == "1":
        return True
    return bool(getattr(sys, "frozen", False))


def python_path(home_dir: Path | str | None = None) -> Path:
    """Where the managed interpreter lives (whether or not it exists yet)."""
    override = os.environ.get("REMEDY_VOICE_PYTHON")
    if override:
        return Path(override)
    root = runtime_dir(home_dir) / "python"
    if platform.system() == "Windows":
        return root / "python.exe"
    return root / "bin" / "python3"


def _marker_path(home_dir: Path | str | None) -> Path:
    return runtime_dir(home_dir) / _MARKER


def read_marker(home_dir: Path | str | None = None) -> dict[str, Any]:
    p = _marker_path(home_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_marker(home_dir: Path | str | None, data: dict[str, Any]) -> None:
    from remedy.core.atomic_json import write_text_atomic

    p = _marker_path(home_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, json.dumps(data, indent=2) + "\n")


def runtime_ready(home_dir: Path | str | None = None) -> bool:
    """The interpreter is on disk and was verified when it landed."""
    if os.environ.get("REMEDY_VOICE_PYTHON"):
        return python_path(home_dir).is_file()
    return python_path(home_dir).is_file() and bool(read_marker(home_dir).get("ok"))


def pack_installed(pack: str, home_dir: Path | str | None = None) -> bool:
    """Was *pack* ("voice" / "hq") pip-installed into the runtime successfully?"""
    packs = read_marker(home_dir).get("packs")
    return isinstance(packs, dict) and bool(packs.get(pack))


def mark_pack(pack: str, ok: bool, home_dir: Path | str | None = None) -> None:
    data = read_marker(home_dir)
    raw = data.get("packs")
    packs: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    packs[pack] = bool(ok)
    data["packs"] = packs
    _write_marker(home_dir, data)


def unsupported_reason() -> str | None:
    """Plain words when this machine has no pinned runtime, else None."""
    if pin_for_this_machine() is None:
        return (
            "Remedy's voice is not available for this kind of computer yet "
            f"({platform.system()} {platform.machine()})."
        )
    return None


# ---------------------------------------------------------------------------
# Download + verify + unpack
# ---------------------------------------------------------------------------

Progress = Callable[[float, str], None]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(pin: RuntimePin, dest: Path, progress: Progress | None) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(pin.url, headers={"User-Agent": "remedy-voice"})
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                done += len(chunk)
                if done > pin.size:
                    raise ValueError(
                        f"{pin.filename} is larger than the pinned {pin.size} bytes"
                    )
                digest.update(chunk)
                f.write(chunk)
                if progress:
                    progress(done * 100 / pin.size, "Setting up Remedy's voice")
        if done != pin.size:
            raise ValueError(f"{pin.filename}: expected {pin.size} bytes, got {done}")
        if digest.hexdigest() != pin.sha256:
            raise ValueError(f"{pin.filename}: sha256 does not match the pin")
        tmp.replace(dest)
    except BaseException:
        # A half or wrong file must never be mistaken for the runtime later.
        tmp.unlink(missing_ok=True)
        raise


def _safe_extract(archive: Path, into: Path) -> None:
    """Unpack, refusing any member that would land outside *into*."""
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            target = (root / m.name).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"archive member escapes runtime dir: {m.name}")
            if m.issym() or m.islnk():
                link_target = (target.parent / m.linkname).resolve()
                if root != link_target and root not in link_target.parents:
                    raise ValueError(f"archive link escapes runtime dir: {m.name}")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(root, filter="data")
        else:  # pragma: no cover - 3.11 and older
            tar.extractall(root)


def install_runtime(
    home_dir: Path | str | None = None,
    *,
    progress: Progress | None = None,
) -> Path:
    """Download + verify + unpack the pinned interpreter. Idempotent.

    Returns the interpreter path. Raises with a plain message the owner can
    act on (callers route it through ``_owner_pack_error``).
    """
    with _lock:
        py = python_path(home_dir)
        if runtime_ready(home_dir):
            return py
        pin = pin_for_this_machine()
        if pin is None:
            raise RuntimeError(unsupported_reason() or "unsupported platform")
        rdir = runtime_dir(home_dir)
        rdir.mkdir(parents=True, exist_ok=True)
        archive = rdir / pin.filename
        if not archive.is_file() or _sha256_file(archive) != pin.sha256:
            archive.unlink(missing_ok=True)
            _download(pin, archive, progress)
        target = rdir / "python"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if progress:
            progress(100.0, "Unpacking Remedy's voice")
        # install_only archives unpack to ./python/...
        _safe_extract(archive, rdir)
        if not py.is_file():
            raise RuntimeError("The voice runtime unpacked without a Python inside.")
        # Smoke: it must start and report the pinned version.
        from remedy.execution.process import run_hidden

        out = run_hidden(
            [str(py), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=60,
            env=child_env(home_dir, with_source=False),
        )
        got = (out.stdout or "").strip()
        if out.returncode != 0 or not got.startswith("3.12"):
            raise RuntimeError(f"The voice runtime did not start ({got or out.stderr[:120]}).")
        archive.unlink(missing_ok=True)
        _write_marker(
            home_dir,
            {
                "ok": True,
                "python": got,
                "tag": _PBS_TAG,
                "triple": pin.triple,
                "packs": read_marker(home_dir).get("packs") or {},
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        return py


# ---------------------------------------------------------------------------
# Source staging + a clean child environment
# ---------------------------------------------------------------------------

_PYI_ENV = ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL")


def source_root_for_worker(home_dir: Path | str | None = None) -> Path:
    """Directory holding the ``remedy`` *source* the worker should import.

    Dev: the checkout's ``src``. Frozen: PyInstaller's ``_MEIPASS`` also
    holds the sidecar's own (3.13) extension modules, so it must never be on
    the 3.12 worker's path — the pure ``.py`` tree is staged into
    ``runtime/app/`` instead, refreshed whenever the sidecar version changes.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not (getattr(sys, "frozen", False) and meipass):
        import remedy

        return Path(remedy.__file__).resolve().parent.parent
    import remedy

    src = Path(meipass) / "remedy"
    app = runtime_dir(home_dir) / "app"
    stamp = app / "version.txt"
    want = str(remedy.__version__)
    try:
        have = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        have = ""
    if have != want or not (app / "remedy" / "voice" / "worker.py").is_file():
        with _lock:
            tmp = app.with_name("app.tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(
                src,
                tmp / "remedy",
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.pyd", "*.so", "*.dll", "*.dylib"
                ),
            )
            (tmp / "version.txt").write_text(want, encoding="utf-8")
            shutil.rmtree(app, ignore_errors=True)
            tmp.replace(app)
    return app


def child_env(home_dir: Path | str | None = None, *, with_source: bool) -> dict[str, str]:
    """Environment for the managed interpreter: nothing of the sidecar's leaks in."""
    env = os.environ.copy()
    for k in _PYI_ENV:
        env.pop(k, None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.pop("REMEDY_VOICE_MANAGED", None)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # PyInstaller prepends its unpack dir to PATH for DLL lookup; the
        # worker must not see python313.dll before its own python312.dll.
        sep = os.pathsep
        env["PATH"] = sep.join(
            p for p in env.get("PATH", "").split(sep) if p and Path(p) != Path(meipass)
        )
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    # No progress bars on stderr: they would flood the sidecar log and
    # nobody is watching a terminal.
    env["TQDM_DISABLE"] = "1"
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    if with_source:
        env["PYTHONPATH"] = str(source_root_for_worker(home_dir))
    if home_dir:
        env["REMEDY_HOME"] = str(home_dir)
    return env


def remove_runtime(home_dir: Path | str | None = None) -> None:
    """Delete the managed runtime (uninstall / reset)."""
    with _lock:
        shutil.rmtree(runtime_dir(home_dir), ignore_errors=True)

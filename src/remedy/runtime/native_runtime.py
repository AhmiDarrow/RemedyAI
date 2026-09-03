"""Layered Go/Zig runtime selection and the ``remedy_core`` library loader.

Compatibility remains the default for the Go runtime. ``auto`` and ``native``
require both the packaged Go probe and the versioned Zig C ABI before native
work is attempted through :func:`execute_with_fallback`.

The Zig host surface (capture, input, windows, clipboard, hidden processes)
is not optional on Windows: :func:`core_library` loads ``remedy_core`` at ABI
:data:`_ABI_VERSION` and raises :class:`NativeRuntimeUnavailableError` when the
library is missing or too old. Bindings call it directly; there is no Python
implementation behind it any more.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from remedy.execution.process import run_hidden

#: Zig C ABI (``remedy_core_abi_version``); see ``native/zig/include/remedy_core.h``.
_ABI_VERSION = 4
#: Go runtime probe contract (``--probe`` JSON ``protocol`` / ``tool_abi``).
_PROTOCOL_VERSION = 1
_TOOL_ABI_VERSION = 1
_SYSTEM_READ = 1 << 3
_CACHE_TTL_SECONDS = 30.0


class NativeRuntimeMode(StrEnum):
    COMPATIBILITY = "compatibility"
    AUTO = "auto"
    NATIVE = "native"


class NativeExecutionError(RuntimeError):
    """Native work failed where replaying it might duplicate a side effect."""


class NativeRuntimeUnavailableError(RuntimeError):
    """``remedy_core`` could not be loaded at the ABI this build requires."""


@dataclass(frozen=True)
class _ComponentProbe:
    ready: bool
    reason: str = ""
    detail: Mapping[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ready": self.ready}
        if self.reason:
            result["reason"] = self.reason
        if self.detail:
            result.update(self.detail)
        return result


_probe_lock = threading.Lock()
_probe_cache: tuple[float, str, dict[str, Any]] | None = None
_config_lock = threading.Lock()
_runtime_config: dict[str, Any] = {}
_library_lock = threading.Lock()
_library_cache: tuple[Path, Any] | None = None


def configured_mode(config: Mapping[str, Any] | None = None) -> NativeRuntimeMode:
    """Resolve env-over-config selection; invalid values fail closed to compatibility."""
    raw = os.environ.get("REMEDY_NATIVE_RUNTIME")
    if config is None:
        with _config_lock:
            config = dict(_runtime_config)
    if raw is None and config is not None:
        raw = str(config.get("native_runtime") or "")
    normalized = (raw or "compatibility").strip().lower()
    aliases = {"compat": "compatibility", "python": "compatibility"}
    normalized = aliases.get(normalized, normalized)
    try:
        return NativeRuntimeMode(normalized)
    except ValueError:
        return NativeRuntimeMode.COMPATIBILITY


def _candidate_roots() -> list[Path]:
    roots = [Path(sys.executable).resolve().parent]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    roots.append(Path(__file__).resolve().parents[3] / "desktop" / "bin")
    return list(dict.fromkeys(roots))


def _resolve_component(env_name: str, names: tuple[str, ...]) -> Path | None:
    explicit = os.environ.get(env_name)
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    for root in _candidate_roots():
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _probe_go() -> _ComponentProbe:
    names = ("remedy-runtime.exe",) if sys.platform == "win32" else ("remedy-runtime",)
    executable = _resolve_component("REMEDY_NATIVE_RUNTIME_BIN", names)
    if executable is None:
        return _ComponentProbe(False, "not-installed")
    try:
        completed = run_hidden(
            [str(executable), "--probe"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        return _ComponentProbe(False, "timeout")
    except (OSError, ValueError):
        return _ComponentProbe(False, "launch-failed")
    if completed.returncode != 0:
        return _ComponentProbe(False, "probe-failed")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return _ComponentProbe(False, "invalid-probe")
    if (
        payload.get("status") != "ready"
        or payload.get("protocol") != _PROTOCOL_VERSION
        or payload.get("tool_abi") != _TOOL_ABI_VERSION
    ):
        return _ComponentProbe(False, "version-mismatch")
    return _ComponentProbe(
        True,
        detail={
            "protocol": _PROTOCOL_VERSION,
            "tool_abi": _TOOL_ABI_VERSION,
            "platform": f"{payload.get('os', 'unknown')}/{payload.get('arch', 'unknown')}",
        },
    )


def _core_library_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("remedy_core.dll",)
    if sys.platform == "darwin":
        return ("libremedy_core.dylib",)
    return ("libremedy_core.so",)


def _dev_checkout_root() -> Path:
    """``native/zig/zig-out`` of a source checkout: DLLs land in bin, .so in lib."""
    zig_out = Path(__file__).resolve().parents[3] / "native" / "zig" / "zig-out"
    return zig_out / ("bin" if sys.platform == "win32" else "lib")


def _core_library_path() -> Path | None:
    """REMEDY_NATIVE_CORE_LIB, then the packaged roots, then a dev checkout."""
    explicit = os.environ.get("REMEDY_NATIVE_CORE_LIB")
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    names = _core_library_names()
    for root in [*_candidate_roots(), _dev_checkout_root()]:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


class _AbiMismatchError(NativeRuntimeUnavailableError):
    def __init__(self, abi: int, library_path: Path | None = None) -> None:
        where = f" at {library_path}" if library_path is not None else ""
        super().__init__(
            f"remedy_core{where} reports ABI {abi}; required ABI is {_ABI_VERSION}. "
            f"Remove stale copies of the library from the search path and rebuild."
        )
        self.abi = abi


def _open_core_library(library_path: Path) -> Any:
    """Load the library and check its ABI; raises NativeRuntimeUnavailableError."""
    global _library_cache
    with _library_lock:
        cached = _library_cache
        if cached is not None and cached[0] == library_path:
            return cached[1]
        try:
            library = ctypes.CDLL(str(library_path))
            library.remedy_core_abi_version.argtypes = []
            library.remedy_core_abi_version.restype = ctypes.c_uint32
            abi = int(library.remedy_core_abi_version())
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise NativeRuntimeUnavailableError(
                f"remedy_core failed to load ({exc.__class__.__name__}: {exc})"
            ) from exc
        if abi != _ABI_VERSION:
            raise _AbiMismatchError(abi, library_path)
        _library_cache = (library_path, library)
        return library


def core_library() -> Any:
    """The loaded ``remedy_core`` ctypes library at ABI :data:`_ABI_VERSION`.

    Search order: ``REMEDY_NATIVE_CORE_LIB``, the packaged roots next to the
    interpreter / bundle / ``desktop/bin``, then ``native/zig/zig-out`` of a
    source checkout. Raises :class:`NativeRuntimeUnavailableError` when no library
    is found or the ABI differs; there is no Python fallback behind it.
    """
    library_path = _core_library_path()
    if library_path is None:
        raise NativeRuntimeUnavailableError(
            "remedy_core library not found: build it with 'zig build "
            "-Doptimize=ReleaseSafe' in native/zig or set REMEDY_NATIVE_CORE_LIB"
        )
    return _open_core_library(library_path)


def _load_zig() -> tuple[_ComponentProbe, Any | None]:
    library_path = _core_library_path()
    if library_path is None:
        return _ComponentProbe(False, "not-installed"), None
    try:
        library = _open_core_library(library_path)
    except _AbiMismatchError as exc:
        return _ComponentProbe(False, "version-mismatch", {"abi": exc.abi}), None
    except NativeRuntimeUnavailableError:
        return _ComponentProbe(False, "load-failed"), None
    return _ComponentProbe(True, detail={"abi": _ABI_VERSION}), library


def native_runtime_status(
    config: Mapping[str, Any] | None = None,
    *,
    force: bool = False,
    probe: bool = True,
) -> dict[str, Any]:
    """Return cached public cutover evidence without exposing local paths."""
    mode = configured_mode(config)
    if mode is NativeRuntimeMode.COMPATIBILITY:
        return {
            "requested": mode.value,
            "effective": "compatibility",
            "ready": True,
            "components": {"go": {"ready": False, "reason": "not-requested"}, "zig": {"ready": False, "reason": "not-requested"}},
        }

    global _probe_cache
    now = time.monotonic()
    cached = _probe_cache
    if not force and cached is not None and cached[1] == mode.value and now - cached[0] < _CACHE_TTL_SECONDS:
        return dict(cached[2])
    if not probe:
        return {
            "requested": mode.value,
            "effective": "compatibility",
            "ready": False,
            "fallback": "probe-pending",
            "components": {
                "go": {"ready": False, "reason": "probe-pending"},
                "zig": {"ready": False, "reason": "probe-pending"},
            },
        }
    with _probe_lock:
        cached = _probe_cache
        if not force and cached is not None and cached[1] == mode.value and now - cached[0] < _CACHE_TTL_SECONDS:
            return dict(cached[2])
        go_probe = _probe_go()
        zig_probe, _ = _load_zig()
        native_ready = go_probe.ready and zig_probe.ready
        payload: dict[str, Any] = {
            "requested": mode.value,
            "effective": "native" if native_ready else "compatibility",
            "ready": native_ready,
            "components": {"go": go_probe.public(), "zig": zig_probe.public()},
        }
        if not native_ready:
            payload["fallback"] = "native-unavailable"
        _probe_cache = (now, mode.value, payload)
        return dict(payload)


def invalidate_native_runtime_cache(*, reset_config: bool = False) -> None:
    global _probe_cache, _runtime_config, _library_cache
    with _probe_lock:
        _probe_cache = None
    if reset_config:
        with _config_lock:
            _runtime_config = {}
        with _library_lock:
            _library_cache = None


def initialize_native_runtime(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Prime one bounded startup probe and retain the product configuration.

    The compatibility default returns without launching anything. ``auto`` and
    ``native`` probe only already-bundled components; they never download code.
    """
    global _runtime_config
    with _config_lock:
        _runtime_config = dict(config or {})
    return native_runtime_status(force=True)


def execute_with_fallback[T](
    native_operation: Callable[[], T],
    compatibility_operation: Callable[[], T],
    *,
    idempotent: bool,
    status: Mapping[str, Any] | None = None,
) -> T:
    """Run native when ready; replay a failed attempt only when explicitly safe."""
    selected = native_runtime_status() if status is None else status
    if selected.get("effective") != "native":
        return compatibility_operation()
    try:
        return native_operation()
    except Exception as exc:
        if not idempotent:
            raise NativeExecutionError(
                "native operation failed; compatibility replay blocked"
            ) from exc
        return compatibility_operation()


def logical_cpu_count(config: Mapping[str, Any] | None = None) -> int:
    """Read CPU topology through the first safe native vertical slice."""
    status = native_runtime_status(config)

    def _native() -> int:
        probe, library = _load_zig()
        if not probe.ready or library is None:
            raise NativeExecutionError("Zig core became unavailable")
        library.remedy_core_logical_cpu_count.argtypes = [
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        library.remedy_core_logical_cpu_count.restype = ctypes.c_int32
        output = ctypes.c_size_t()
        result = int(
            library.remedy_core_logical_cpu_count(_SYSTEM_READ, ctypes.byref(output))
        )
        if result != 0 or output.value < 1:
            raise NativeExecutionError("Zig system probe failed")
        return int(output.value)

    return execute_with_fallback(
        _native,
        lambda: os.cpu_count() or 1,
        idempotent=True,
        status=status,
    )

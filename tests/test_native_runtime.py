from __future__ import annotations

import os
import subprocess
from unittest.mock import Mock

import pytest

from remedy.runtime import native_runtime


@pytest.fixture(autouse=True)
def _clear_native_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REMEDY_NATIVE_RUNTIME", raising=False)
    monkeypatch.delenv("REMEDY_NATIVE_RUNTIME_BIN", raising=False)
    monkeypatch.delenv("REMEDY_NATIVE_CORE_LIB", raising=False)
    native_runtime.invalidate_native_runtime_cache(reset_config=True)
    yield
    native_runtime.invalidate_native_runtime_cache(reset_config=True)


def test_compatibility_is_default_and_never_probes(monkeypatch: pytest.MonkeyPatch):
    go_probe = Mock()
    zig_probe = Mock()
    monkeypatch.setattr(native_runtime, "_probe_go", go_probe)
    monkeypatch.setattr(native_runtime, "_load_zig", zig_probe)

    status = native_runtime.native_runtime_status()

    assert status["requested"] == "compatibility"
    assert status["effective"] == "compatibility"
    go_probe.assert_not_called()
    zig_probe.assert_not_called()


def test_auto_requires_go_and_zig(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME", "auto")
    monkeypatch.setattr(
        native_runtime,
        "_probe_go",
        lambda: native_runtime._ComponentProbe(True, detail={"protocol": 1}),
    )
    monkeypatch.setattr(
        native_runtime,
        "_load_zig",
        lambda: (native_runtime._ComponentProbe(True, detail={"abi": 2}), object()),
    )

    status = native_runtime.native_runtime_status()

    assert status["effective"] == "native"
    assert status["ready"] is True


def test_nonblocking_status_never_starts_probe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME", "auto")
    go_probe = Mock()
    zig_probe = Mock()
    monkeypatch.setattr(native_runtime, "_probe_go", go_probe)
    monkeypatch.setattr(native_runtime, "_load_zig", zig_probe)

    status = native_runtime.native_runtime_status(probe=False)

    assert status["effective"] == "compatibility"
    assert status["fallback"] == "probe-pending"
    go_probe.assert_not_called()
    zig_probe.assert_not_called()


def test_startup_initialization_primes_configured_selector(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        native_runtime,
        "_probe_go",
        lambda: native_runtime._ComponentProbe(True, detail={"protocol": 1}),
    )
    monkeypatch.setattr(
        native_runtime,
        "_load_zig",
        lambda: (native_runtime._ComponentProbe(True, detail={"abi": 2}), object()),
    )

    initialized = native_runtime.initialize_native_runtime({"native_runtime": "auto"})
    cached = native_runtime.native_runtime_status(probe=False)

    assert initialized["effective"] == "native"
    assert cached["effective"] == "native"


def test_auto_falls_back_with_public_evidence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME", "auto")
    monkeypatch.setattr(
        native_runtime,
        "_probe_go",
        lambda: native_runtime._ComponentProbe(False, "not-installed"),
    )
    monkeypatch.setattr(
        native_runtime,
        "_load_zig",
        lambda: (native_runtime._ComponentProbe(True, detail={"abi": 2}), object()),
    )

    status = native_runtime.native_runtime_status()

    assert status["effective"] == "compatibility"
    assert status["fallback"] == "native-unavailable"
    assert status["components"]["go"] == {"ready": False, "reason": "not-installed"}


def test_go_probe_timeout_is_safe_and_path_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
):
    executable = tmp_path / "remedy-runtime.exe"
    executable.touch()
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(executable))
    monkeypatch.setattr(
        native_runtime,
        "run_hidden",
        Mock(side_effect=subprocess.TimeoutExpired([str(executable), "--probe"], 2.0)),
    )

    result = native_runtime._probe_go().public()

    assert result == {"ready": False, "reason": "timeout"}
    assert str(tmp_path) not in str(result)


def test_failed_native_replays_only_idempotent_work():
    compatibility = Mock(return_value="compat")

    assert (
        native_runtime.execute_with_fallback(
            Mock(side_effect=OSError("failed")),
            compatibility,
            idempotent=True,
            status={"effective": "native"},
        )
        == "compat"
    )
    compatibility.assert_called_once_with()

    compatibility.reset_mock()
    with pytest.raises(native_runtime.NativeExecutionError):
        native_runtime.execute_with_fallback(
            Mock(side_effect=OSError("partial")),
            compatibility,
            idempotent=False,
            status={"effective": "native"},
        )
    compatibility.assert_not_called()


def test_compatibility_primary_path_allows_non_idempotent_work():
    compatibility = Mock(return_value="done")
    assert (
        native_runtime.execute_with_fallback(
            Mock(),
            compatibility,
            idempotent=False,
            status={"effective": "compatibility"},
        )
        == "done"
    )


def test_logical_cpu_count_uses_compatibility_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 7)
    assert native_runtime.logical_cpu_count() == 7


# --- remedy_core loader (ABI 2) ------------------------------------------


def test_the_python_side_requires_abi_five_from_the_zig_core():
    assert native_runtime._ABI_VERSION == 5
    # The Go probe contract is a separate version and did not move.
    assert native_runtime._TOOL_ABI_VERSION == 1
    assert native_runtime._PROTOCOL_VERSION == 1


def test_core_library_fails_clearly_when_the_library_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
):
    monkeypatch.setenv("REMEDY_NATIVE_CORE_LIB", str(tmp_path / "missing" / "remedy_core.dll"))
    with pytest.raises(native_runtime.NativeRuntimeUnavailableError, match="not found"):
        native_runtime.core_library()
    probe, library = native_runtime._load_zig()
    assert probe.public() == {"ready": False, "reason": "not-installed"}
    assert library is None


def test_core_library_rejects_a_library_at_the_wrong_abi(monkeypatch: pytest.MonkeyPatch, tmp_path):
    fake = tmp_path / "remedy_core.dll"
    fake.write_bytes(b"")
    monkeypatch.setenv("REMEDY_NATIVE_CORE_LIB", str(fake))

    class _OldLibrary:
        class remedy_core_abi_version:  # noqa: N801 - mirrors the C symbol
            argtypes: list = []
            restype = None

            def __call__(self):
                return 1

        remedy_core_abi_version = remedy_core_abi_version()

    monkeypatch.setattr(native_runtime.ctypes, "CDLL", lambda _path: _OldLibrary())
    with pytest.raises(native_runtime.NativeRuntimeUnavailableError, match="ABI 1"):
        native_runtime.core_library()
    probe, _ = native_runtime._load_zig()
    assert probe.public() == {"ready": False, "reason": "version-mismatch", "abi": 1}


def test_core_library_search_order_ends_at_the_dev_checkout():
    root = native_runtime._dev_checkout_root()
    assert root.parts[-3:-1] == ("zig", "zig-out")
    assert root.name == ("bin" if native_runtime.sys.platform == "win32" else "lib")


def test_core_library_loads_the_built_core_when_present(
    monkeypatch: pytest.MonkeyPatch,
):
    # Drop any cached handle so a prior soname collision cannot stick.
    monkeypatch.setattr(native_runtime, "_library_cache", None)
    path = native_runtime._core_library_path()
    if path is None:
        pytest.skip("remedy_core is not built in this checkout")
    library = native_runtime.core_library()
    assert int(library.remedy_core_abi_version()) == 5, f"loaded from {path}"
    assert native_runtime.core_library() is library

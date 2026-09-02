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
    native_runtime.invalidate_native_runtime_cache()
    yield
    native_runtime.invalidate_native_runtime_cache()


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
        lambda: (native_runtime._ComponentProbe(True, detail={"abi": 1}), object()),
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
        lambda: (native_runtime._ComponentProbe(True, detail={"abi": 1}), object()),
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

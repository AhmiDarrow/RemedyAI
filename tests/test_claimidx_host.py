from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

from remedy.runtime import claimidx_host as host


class _Response:
    status = 200

    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def _completed(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_clean_env_isolates_global_claimidx_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIMIDX_HOME_TOKEN", "owner-secret")
    monkeypatch.setenv("CLAIMIDX_HOME_API", "https://private.example")
    monkeypatch.setenv("CLAIMIDX_SHARE", "1")
    monkeypatch.setattr(host, "_service_token", lambda _home=None: "spt_managed_token_value")

    env = host._clean_env(tmp_path)

    assert env["CLAIMIDX_CONFIG"] == str(tmp_path / "claimidx" / "config.json")
    assert env["CLAIMIDX_DB"] == str(tmp_path / "claimidx" / "index.sqlite")
    assert env["CLAIMIDX_HOME_TOKEN"] == "spt_managed_token_value"
    assert env["CLAIMIDX_SHARE"] == "0"
    assert "CLAIMIDX_HOME_API" not in env


def test_service_token_is_stable_and_private(tmp_path):
    first = host._service_token(tmp_path)
    second = host._service_token(tmp_path)

    assert first == second
    assert first.startswith("spt_")
    assert len(first) >= 24
    assert (tmp_path / "claimidx" / "service-token").read_text().strip() == first


def test_download_wheel_verifies_pinned_digest(monkeypatch, tmp_path):
    payload = b"a pinned Claimidx wheel"
    monkeypatch.setattr(host, "CLAIMIDX_WHEEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(host, "urlopen", lambda *_a, **_k: _Response(payload))
    destination = tmp_path / "claimidx.whl"

    host._download_wheel(destination)

    assert destination.read_bytes() == payload


def test_download_wheel_rejects_digest_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "urlopen", lambda *_a, **_k: _Response(b"tampered"))

    try:
        host._download_wheel(tmp_path / "claimidx.whl")
    except RuntimeError as exc:
        assert "sha256" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("tampered wheel was accepted")


def test_ensure_installed_marks_only_a_verified_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(host, "_base_python", lambda _home=None: Path("base-python"))
    monkeypatch.setattr(host, "_download_wheel", lambda path: path.write_bytes(b"wheel"))
    calls: list[list[str]] = []

    def fake_run(args, _env, *, timeout):
        calls.append(args)
        if args[1:3] == ["-m", "venv"]:
            py = host._venv_python(tmp_path)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_bytes(b"python")
        if "import claimidx, fastapi, uvicorn" in args[-1]:
            return _completed(host.CLAIMIDX_VERSION + "\n")
        return _completed()

    monkeypatch.setattr(host, "_run", fake_run)

    result = host.ensure_installed(tmp_path)

    assert result["ok"] is True
    assert result["downloaded"] is True
    assert any("pip" in call and "install" in call for call in calls)
    assert host._installed(tmp_path)


def test_ensure_installed_reports_interrupted_runtime_cleanup_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    runtime = host._runtime_dir(tmp_path)
    runtime.mkdir(parents=True)
    monkeypatch.setattr(host.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("busy")))

    result = host.ensure_installed(tmp_path)

    assert result["ok"] is False
    assert "busy" in result["error"]


def test_setup_is_offline_private_and_uses_module_entrypoint(monkeypatch, tmp_path):
    py = host._venv_python(tmp_path)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"python")
    calls: list[list[str]] = []

    def fake_run(args, _env, *, timeout):
        calls.append(args)
        return _completed('{"imported": 1}')

    monkeypatch.setattr(host, "_run", fake_run)
    monkeypatch.setattr(host, "_service_token", lambda _home=None: "spt_test_token_value")

    result = host._setup(tmp_path)

    assert result == {"ok": True, "seeded": True}
    assert calls[0][1:3] == ["-m", "claimidx.cli"]
    assert calls[0][-1] == "seed"
    config = json.loads((tmp_path / "claimidx" / "config.json").read_text())
    assert config == {
        "owner": "did:claimidx:remedy",
        "agent": "remedy",
        "share": False,
    }


def test_health_check_only_adopts_claimidx(monkeypatch):
    monkeypatch.setattr(
        host,
        "urlopen",
        lambda *_a, **_k: _Response(b'{"product":"something-else"}'),
    )
    assert host.is_healthy() is False

    monkeypatch.setattr(
        host,
        "urlopen",
        lambda *_a, **_k: _Response(
            b'{"product":"Claimidx","operator":{"did":"did:claimidx:someone-else",'
            b'"agent":"someone-else"}}'
        ),
    )
    assert host.is_healthy() is False

    monkeypatch.setattr(
        host,
        "urlopen",
        lambda *_a, **_k: _Response(
            b'{"product":"Claimidx","operator":{"did":"did:claimidx:remedy",'
            b'"agent":"remedy"}}'
        ),
    )
    assert host.is_healthy() is True


def test_start_installs_and_seeds_before_adopting_managed_listener(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        host,
        "ensure_installed",
        lambda _home=None: calls.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        host,
        "_setup",
        lambda _home=None: calls.append("seed") or {"ok": True},
    )
    monkeypatch.setattr(host, "is_healthy", lambda: calls.append("health") or True)

    result = host.start(tmp_path)

    assert result == {"ok": True, "already": True, "url": host.base_url()}
    assert calls == ["install", "seed", "health"]


def test_schedule_is_non_blocking_and_once(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "_ensure_started", False)
    monkeypatch.setattr(host, "_skip_network", lambda: False)
    starts: list[Path] = []
    monkeypatch.setattr(
        host,
        "start",
        lambda home=None: starts.append(Path(home)) or {"ok": True, "url": host.base_url()},
    )

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(host.threading, "Thread", InlineThread)

    host.schedule_ensure(tmp_path)
    host.schedule_ensure(tmp_path)

    assert starts == [tmp_path]

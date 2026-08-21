"""The managed voice runtime: pins, marker, safe unpack, worker wire, bridge.

Nothing here touches the network or a real engine. The worker and bridge
are exercised with this interpreter standing in for the managed Python.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

from remedy.voice import runtime as rt


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.delenv("REMEDY_VOICE_PYTHON", raising=False)
    monkeypatch.delenv("REMEDY_VOICE_MANAGED", raising=False)
    monkeypatch.delenv("REMEDY_VOICE_WORKER", raising=False)
    return tmp_path


# -- decisions ---------------------------------------------------------------


def test_dev_is_in_process_and_desktop_is_managed(home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert rt.use_managed_runtime() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert rt.use_managed_runtime() is True
    # The worker never recurses into itself.
    monkeypatch.setenv("REMEDY_VOICE_WORKER", "1")
    assert rt.use_managed_runtime() is False


def test_env_can_force_the_managed_path_from_a_checkout(home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("REMEDY_VOICE_MANAGED", "1")
    assert rt.use_managed_runtime() is True


def test_every_pin_has_a_real_sha_and_a_github_url():
    for _key, pin in rt._PINS.items():
        assert len(pin.sha256) == 64 and int(pin.sha256, 16)
        assert pin.size > 10_000_000
        assert pin.url.startswith("https://github.com/astral-sh/python-build-standalone/")
        assert "%2B" in pin.url and "+" not in pin.url.split("/")[-1]
        assert pin.filename.endswith("install_only_stripped.tar.gz")


def test_this_machine_has_a_pin_or_an_honest_reason():
    pin = rt.pin_for_this_machine()
    reason = rt.unsupported_reason()
    assert (pin is not None and reason is None) or (pin is None and "not available" in reason)


# -- marker ------------------------------------------------------------------


def test_runtime_is_not_ready_until_verified(home):
    py = rt.python_path(home)
    assert rt.runtime_ready(home) is False
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    # On disk but never verified: still not ready.
    assert rt.runtime_ready(home) is False
    rt._write_marker(home, {"ok": True})
    assert rt.runtime_ready(home) is True


def test_packs_are_remembered_per_name(home):
    assert rt.pack_installed("voice", home) is False
    rt.mark_pack("voice", True, home)
    rt.mark_pack("hq", False, home)
    assert rt.pack_installed("voice", home) is True
    assert rt.pack_installed("hq", home) is False
    # Marking a pack never loses the runtime's own fields.
    rt._write_marker(home, {"ok": True, "python": "3.12.14", "packs": {"voice": True}})
    rt.mark_pack("hq", True, home)
    assert rt.read_marker(home) == {"ok": True, "python": "3.12.14", "packs": {"voice": True, "hq": True}}


def test_a_junk_marker_reads_as_empty(home):
    p = rt._marker_path(home)
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2]", encoding="utf-8")
    assert rt.read_marker(home) == {}
    p.write_text("{not json", encoding="utf-8")
    assert rt.read_marker(home) == {}


def test_python_override_wins(home, monkeypatch):
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    assert rt.python_path(home) == Path(sys.executable)
    assert rt.runtime_ready(home) is True


# -- unpack safety -------------------------------------------------------------


def _tar_with(members: dict[str, bytes]) -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf


def test_safe_extract_refuses_escapes(tmp_path: Path):
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(_tar_with({"python/ok.txt": b"x", "../escape.txt": b"y"}).getvalue())
    with pytest.raises(ValueError, match="escapes"):
        rt._safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_unpacks_good_archives(tmp_path: Path):
    archive = tmp_path / "good.tar.gz"
    archive.write_bytes(_tar_with({"python/bin/python3": b"#!", "python/lib/a.py": b"1"}).getvalue())
    rt._safe_extract(archive, tmp_path / "out")
    assert (tmp_path / "out" / "python" / "bin" / "python3").read_bytes() == b"#!"


def test_install_runtime_rejects_a_tampered_download(home, monkeypatch):
    pin = rt.RuntimePin("x86_64-test", "0" * 64, 3)
    monkeypatch.setattr(rt, "pin_for_this_machine", lambda: pin)

    class _Resp(io.BytesIO):
        headers = {"Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"abc"))
    with pytest.raises(ValueError, match="sha256"):
        rt.install_runtime(home)
    assert rt.runtime_ready(home) is False
    assert not list((rt.runtime_dir(home)).glob("*.part"))


# -- worker wire ---------------------------------------------------------------


def test_worker_handles_bad_lines_and_unknown_ops():
    from remedy.voice import worker

    assert worker.handle("{nope")["ok"] is False
    out = worker.handle(json.dumps({"id": 7, "op": "nothing"}))
    assert out == {"id": 7, "ok": False, "error": "unknown op 'nothing'"}
    pong = worker.handle(json.dumps({"id": 1, "op": "ping", "args": {}}))
    assert pong["ok"] and pong["result"]["pid"] == os.getpid()


def test_worker_returns_failures_on_the_wire_not_as_crashes(monkeypatch):
    from remedy.voice import worker

    def boom(args):
        raise RuntimeError("engine fell over")

    monkeypatch.setitem(worker.OPS, "ping", boom)
    out = worker.handle(json.dumps({"id": 2, "op": "ping"}))
    assert out == {"id": 2, "ok": False, "error": "engine fell over"}


# -- bridge ↔ real subprocess ------------------------------------------------


def test_bridge_round_trips_through_a_real_worker_process(home, monkeypatch):
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    from remedy.voice.bridge import VoiceBridge

    b = VoiceBridge(home)
    try:
        pong = b.call("ping", timeout=60)
        assert pong["pid"] != os.getpid()
        probe = b.probe()
        assert set(probe) >= {"tts", "stt", "smart_turn"}
        assert probe["lane"] == "voice"
        # The hq lane is a separate process that never imports whisper.
        from remedy.voice.bridge import LANE_HQ
        from remedy.voice.bridge import VoiceBridge as VB

        hq = VB(home, LANE_HQ)
        try:
            p2 = hq.probe()
            assert p2["lane"] == "hq" and "hq" in p2 and "stt" not in p2
            assert hq.call("ping", timeout=60)["pid"] != pong["pid"]
        finally:
            hq.stop()
        # Ids advance and the same process answers.
        assert b.call("ping", timeout=60)["pid"] == pong["pid"]
    finally:
        b.stop()
    assert b._proc is None


def test_bridge_surfaces_worker_errors_and_recovers(home, monkeypatch):
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    from remedy.voice.bridge import VoiceBridge, WorkerError

    b = VoiceBridge(home)
    try:
        with pytest.raises(WorkerError, match="unknown op"):
            b.call("no_such_op", timeout=60)
        # Still alive and usable after an error reply.
        assert b.call("ping", timeout=60)["pid"]
    finally:
        b.stop()


def test_bridge_respawns_after_the_worker_dies(home, monkeypatch):
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    from remedy.voice.bridge import VoiceBridge

    b = VoiceBridge(home)
    try:
        first = b.call("ping", timeout=60)["pid"]
        assert b._proc is not None
        b._proc.kill()
        b._proc.wait(timeout=10)
        second = b.call("ping", timeout=60)["pid"]
        assert second != first
    finally:
        b.stop()


# -- service wiring ------------------------------------------------------------


def test_desktop_deps_come_from_the_marker_not_imports(home, monkeypatch):
    from remedy.voice import chatterbox as hq
    from remedy.voice import service as svc

    monkeypatch.setenv("REMEDY_VOICE_MANAGED", "1")
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    assert svc.tts_deps_available() is False
    assert svc.stt_deps_available() is False
    assert svc.smart_turn_deps_available() is False
    assert hq.chatterbox_deps_available() is False
    rt.mark_pack("voice", True, home)
    assert svc.tts_deps_available() is True
    assert svc.smart_turn_deps_available() is True
    assert hq.chatterbox_deps_available() is False
    rt.mark_pack("hq", True, home)
    assert hq.chatterbox_deps_available() is True


def test_desktop_status_has_no_pip_hint(home, monkeypatch):
    from remedy.voice import service as svc

    monkeypatch.setenv("REMEDY_VOICE_MANAGED", "1")
    st = svc.voice_status(home)
    assert st["tts"]["available"] is False
    assert st["tts"]["hint"] is None
    assert "pip" not in (st["tts"]["reason"] or "")
    assert "pip" not in (st["hq"]["reason"] or "")
    assert "Update Remedy Desktop" not in (st["hq"]["reason"] or "")


def test_frozen_error_text_never_says_update_and_retry(monkeypatch):
    from remedy.voice.service import _owner_pack_error

    msg = _owner_pack_error(RuntimeError("frozen"), what="The voice pack")
    assert "Update Remedy Desktop" not in msg
    assert msg.endswith(".")


def test_desktop_synthesize_goes_through_the_bridge(home, monkeypatch):
    from remedy.voice import bridge
    from remedy.voice import service as svc

    monkeypatch.setenv("REMEDY_VOICE_MANAGED", "1")
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    rt.mark_pack("voice", True, home)
    model, voices = svc.tts_paths(home)
    model.write_bytes(b"m")
    voices.write_bytes(b"v")

    class FakeBridge:
        def synthesize(self, text, **kw):
            return b"RIFFfake", 24_000

        def transcribe(self, path, **kw):
            return {"text": "heard", "language": "en", "duration": 0.1}

    monkeypatch.setattr(bridge, "get_bridge", lambda home_dir=None, lane="voice": FakeBridge())
    monkeypatch.setattr(svc, "get_tts_engine", lambda *a, **k: pytest.fail("in-process engine used"))
    assert svc.synthesize("hi", home_dir=home) == (b"RIFFfake", 24_000)
    assert svc.transcribe_file(home / "x.wav", home_dir=home)["text"] == "heard"


def test_desktop_pack_install_pips_into_the_runtime(home, monkeypatch):
    from remedy.voice import bridge
    from remedy.voice import service as svc

    monkeypatch.setenv("REMEDY_VOICE_MANAGED", "1")
    monkeypatch.setenv("REMEDY_VOICE_PYTHON", sys.executable)
    seen: dict[str, object] = {}

    def fake_pip(packages, state, key, *, cap=40.0, python=None):
        seen["python"] = python
        seen["packages"] = packages

    stopped: list[str] = []

    class FakeBridge:
        def probe(self):
            return {"tts": True, "stt": True, "smart_turn": True, "hq": False}

    monkeypatch.setattr(svc, "run_pip_packages", fake_pip)
    monkeypatch.setattr(bridge, "get_bridge", lambda home_dir=None, lane="voice": FakeBridge())
    monkeypatch.setattr(bridge, "stop_lane", lambda home_dir, lane: stopped.append(lane))
    monkeypatch.setattr(svc, "install_tts", lambda h=None: None)
    monkeypatch.setattr(svc, "install_stt_background", lambda h=None: True)
    monkeypatch.setattr(svc, "install_smart_turn_background", lambda h=None: True)
    svc.install_voice_pack(home)
    assert Path(str(seen["python"])) == Path(sys.executable)
    assert seen["packages"] == svc._PACK_BASE_PACKAGES + svc._VOICE_PACK_PACKAGES
    assert rt.pack_installed("voice", home) is True
    assert svc._install_state["pack"]["status"] == "done"
    # The voice lane is restarted after pip so it never serves stale imports.
    assert stopped == ["voice"]


def test_run_pip_packages_refuses_to_pip_a_frozen_sidecar_into_itself(monkeypatch):
    from remedy.voice.service import run_pip_packages

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="frozen"):
        run_pip_packages(("x",), {}, "k")

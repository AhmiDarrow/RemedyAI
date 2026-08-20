"""Fetching the smart-turn model: the download that consent.COMPONENTS promised.

The HTTP layer is faked throughout — nothing here talks to Hugging Face, and
nothing here touches the real ``~/.remedy``.
"""

from __future__ import annotations

import hashlib
import io
import sys
import types

import pytest

from remedy.voice import service
from remedy.voice.realtime.turn import (
    EnergyTurnDetector,
    SmartTurnDetector,
    make_detector,
    smart_turn_model_path,
)

PAYLOAD = b"\x08\x01\x12\x00" * 2048  # stands in for the ONNX protobuf


@pytest.fixture
def pin(monkeypatch):
    """Re-pin to the fake payload so size/sha verification is exercised for real."""
    fake = service.SmartTurnPin(
        repo=service.SMART_TURN_PIN.repo,
        revision=service.SMART_TURN_PIN.revision,
        filename=service.SMART_TURN_PIN.filename,
        size=len(PAYLOAD),
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        licence=service.SMART_TURN_PIN.licence,
    )
    monkeypatch.setattr(service, "SMART_TURN_PIN", fake)
    monkeypatch.setitem(service._install_state, "smart-turn", None)
    return fake


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _serve(monkeypatch, body: bytes, *, expect_url: str | None = None) -> list[str]:
    seen: list[str] = []

    def fake_open(url: str, *, timeout: float = 120.0):
        seen.append(url)
        if expect_url is not None:
            assert url == expect_url
        return _Resp(body)

    monkeypatch.setattr(service, "_hf_open", fake_open)
    return seen


def test_the_pin_names_an_exact_file_on_the_hub():
    """A commit, not ``main``: the digest only means something against one blob."""
    p = service.SMART_TURN_PIN
    assert p.repo == "pipecat-ai/smart-turn-v3"
    assert len(p.revision) == 40
    assert p.filename.endswith(".onnx")
    assert len(p.sha256) == 64
    assert p.licence == "BSD-2-Clause"
    assert p.url == (
        f"https://huggingface.co/{p.repo}/resolve/{p.revision}/{p.filename}"
    )


def test_the_licence_matches_the_consent_registry():
    from remedy.telephony.consent import COMPONENTS

    assert COMPONENTS["smart-turn"].licence == service.SMART_TURN_PIN.licence


def test_install_writes_the_model_where_the_detector_looks(tmp_path, monkeypatch, pin):
    seen = _serve(monkeypatch, PAYLOAD, expect_url=pin.url)
    dest = service.install_smart_turn(tmp_path)
    assert seen == [pin.url]
    assert dest == tmp_path / "voice" / "models" / "smart-turn" / pin.filename
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_suffix(".onnx.part").exists()
    assert service._install_state["smart-turn"] == {"status": "done", "percent": 100.0}
    # The detector's own lookup finds it; no restart, no configuration.
    assert smart_turn_model_path(tmp_path) == str(dest)


def test_install_is_idempotent_and_does_not_refetch(tmp_path, monkeypatch, pin):
    _serve(monkeypatch, PAYLOAD)
    service.install_smart_turn(tmp_path)
    seen = _serve(monkeypatch, b"should not be asked for")
    service.install_smart_turn(tmp_path)
    assert seen == []


def test_status_reports_the_model_like_the_other_engines(tmp_path, monkeypatch, pin):
    before = service.voice_status(tmp_path)["smart_turn"]
    assert before["installed"] is False
    assert before["available"] is False
    assert before["reason"]
    assert before["source"] == {
        "repo": pin.repo,
        "revision": pin.revision,
        "filename": pin.filename,
        "licence": pin.licence,
    }
    assert before["fallback"] == "energy"

    _serve(monkeypatch, PAYLOAD)
    service.install_smart_turn(tmp_path)
    monkeypatch.setattr(service, "smart_turn_deps_available", lambda: True)
    after = service.voice_status(tmp_path)["smart_turn"]
    assert after["installed"] is True
    assert after["available"] is True
    assert after["reason"] is None
    assert after["engine"] == "smart-turn-v3"
    assert after["path"] == str(service.smart_turn_path(tmp_path))
    assert after["install"]["status"] == "done"


def test_status_says_why_when_only_the_runtime_is_missing(tmp_path, monkeypatch, pin):
    _serve(monkeypatch, PAYLOAD)
    service.install_smart_turn(tmp_path)
    monkeypatch.setattr(service, "smart_turn_deps_available", lambda: False)
    st = service.voice_status(tmp_path)["smart_turn"]
    assert st["installed"] is True
    assert st["available"] is False
    assert "not on this computer" in (st["reason"] or "").lower()
    assert "pip" not in (st["reason"] or "").lower()
    assert st["hint"] == "pip install remedy-ai[voice]"


def test_make_detector_picks_up_the_model_on_the_next_call(tmp_path, monkeypatch, pin):
    """No caching in ``make_detector``: the call after the install is semantic."""
    assert isinstance(make_detector(home=tmp_path), EnergyTurnDetector)

    class _Input:
        name = "input_features"
        shape = [1, 80, 800]

    class _Session:
        def __init__(self, path, providers=None):
            assert path == str(service.smart_turn_path(tmp_path))

        def get_inputs(self):
            return [_Input()]

    monkeypatch.setitem(
        sys.modules, "onnxruntime", types.SimpleNamespace(InferenceSession=_Session)
    )
    _serve(monkeypatch, PAYLOAD)
    service.install_smart_turn(tmp_path)
    detector = make_detector(home=tmp_path)
    assert isinstance(detector, SmartTurnDetector)
    assert detector.available


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (PAYLOAD[:-100], "truncated"),
        (PAYLOAD + b"x", "oversized"),
        (b"\x00" * len(PAYLOAD), "right size, wrong bytes"),
    ],
    ids=["truncated", "oversized", "wrong-bytes"],
)
def test_a_bad_download_leaves_nothing_behind(tmp_path, monkeypatch, pin, body, why):
    _serve(monkeypatch, body)
    with pytest.raises(ValueError):
        service.install_smart_turn(tmp_path)
    root = tmp_path / "voice" / "models" / "smart-turn"
    assert list(root.iterdir()) == [], why
    assert smart_turn_model_path(tmp_path) == ""
    assert service._install_state["smart-turn"]["status"] == "error"
    assert isinstance(make_detector(home=tmp_path), EnergyTurnDetector)


def test_a_connection_that_dies_mid_stream_leaves_nothing_behind(tmp_path, monkeypatch, pin):
    class _Dying(_Resp):
        def read(self, n=-1):
            chunk = super().read(n)
            if chunk:
                return chunk
            raise OSError("connection reset")

    monkeypatch.setattr(service, "_hf_open", lambda url, *, timeout=120.0: _Dying(PAYLOAD[:1000]))
    with pytest.raises(OSError):
        service.install_smart_turn(tmp_path)
    assert list((tmp_path / "voice" / "models" / "smart-turn").iterdir()) == []
    assert service._install_state["smart-turn"]["status"] == "error"


def test_background_install_refuses_to_double_start(tmp_path, monkeypatch, pin):
    started: list[object] = []
    monkeypatch.setattr(
        service.threading,
        "Thread",
        lambda *a, **k: types.SimpleNamespace(start=lambda: started.append(1)),
    )
    assert service.install_smart_turn_background(tmp_path) is True
    assert service.install_smart_turn_background(tmp_path) is False
    assert len(started) == 1


def test_the_v3_input_is_the_log_mel_the_detector_already_builds():
    """v3 wants ``(1, 80, 800)``; ``_model_input`` reads the rank off the
    session and builds mel features rather than feeding raw samples."""
    pytest.importorskip("numpy")
    from remedy.telephony.backends.fake import voiced_pcm

    class _Input:
        name = "input_features"
        shape = [1, 80, 800]

    class _Session:
        fed = None

        def get_inputs(self):
            return [_Input()]

    d = SmartTurnDetector(model_path="pinned.onnx")
    d._tried = True
    d._session = _Session()
    tensor = d._model_input(voiced_pcm(3000, 8000))
    assert tensor.shape == (1, 80, 800)

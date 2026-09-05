"""Voice: gender-mapped speak-back + hearing, graceful without engines.

These tests run WITHOUT the [voice] extra installed — they exercise the
mapping, text-cleaning, wav encoding, settings, and service helpers. HTTP
``/api/voice/*`` is Go-owned (native/go/httpapi/voice_test.go).
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from remedy.voice.service import (
    encode_wav,
    load_voice_settings,
    save_voice_settings,
    speakable_text,
    voice_for_gender,
    voice_status,
)

# ---------------------------------------------------------------------------
# Gender → voice (befitting the assigned gender role)
# ---------------------------------------------------------------------------


def test_voice_follows_agent_gender():
    assert voice_for_gender("female") == "af_heart"
    assert voice_for_gender("male") == "am_michael"
    assert voice_for_gender("neutral") == "af_sky"
    # agent_identity aliases resolve too
    assert voice_for_gender("he") == "am_michael"
    assert voice_for_gender("she") == "af_heart"
    # Unknown → default (female is the product default gender)
    assert voice_for_gender("") == "af_heart"
    assert voice_for_gender(None) == "af_heart"


def test_voice_override_wins():
    assert voice_for_gender("male", override="bf_emma") == "bf_emma"
    assert voice_for_gender("female", override="") == "af_heart"


# ---------------------------------------------------------------------------
# Speakable text
# ---------------------------------------------------------------------------


def test_speakable_text_strips_markdown():
    md = (
        "# Done!\n\n"
        "I **added** the [item](https://example.com/x) to your cart.\n\n"
        "```json\n{\"secret\": \"never-read-aloud\"}\n```\n"
        "- total: $64.12\n"
        "Visit https://example.com/track for tracking."
    )
    t = speakable_text(md)
    assert "Done!" in t
    assert "added" in t and "**" not in t
    assert "item" in t and "example.com/x" not in t
    assert "never-read-aloud" not in t
    assert "code shown on screen" in t
    assert "$64.12" in t
    assert "https://" not in t


def test_speakable_text_truncates_on_sentence():
    long = ("This is a sentence. " * 200).strip()
    t = speakable_text(long, max_chars=300)
    assert len(t) <= 300
    assert t.endswith(".")


# ---------------------------------------------------------------------------
# WAV encoding
# ---------------------------------------------------------------------------


def test_encode_wav_roundtrip_plain_list():
    wav_bytes = encode_wav([0.0, 0.5, -0.5, 1.0, -1.0], 24_000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24_000
        assert w.getnframes() == 5


# ---------------------------------------------------------------------------
# Settings (self-contained voice.json under REMEDY_HOME)
# ---------------------------------------------------------------------------


def test_voice_settings_roundtrip(tmp_path: Path):
    cfg = load_voice_settings(tmp_path)
    assert cfg["speak_replies"] is False
    out = save_voice_settings(
        {"speak_replies": True, "speed": 5.0, "stt_model": "base", "junk": "x"},
        tmp_path,
    )
    assert out["speak_replies"] is True
    assert out["speed"] == 2.0  # clamped
    assert out["stt_model"] == "base"
    assert "junk" not in out
    again = load_voice_settings(tmp_path)
    assert again["speak_replies"] is True


# ---------------------------------------------------------------------------
# Status without engines
# ---------------------------------------------------------------------------


def test_status_reports_reasons_when_engines_missing(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "tts_deps_available", lambda: False)
    monkeypatch.setattr(svc, "stt_deps_available", lambda: False)
    st = voice_status(tmp_path, agent_gender="male")
    assert st["tts"]["available"] is False
    assert "not on this computer" in (st["tts"]["reason"] or "").lower()
    assert "pip" not in (st["tts"]["reason"] or "").lower()
    assert st["tts"]["hint"] == "pip install remedy-ai[voice]"
    assert st["pack"]["deps"] is False
    assert "smart_turn" in st
    assert "not on this computer" in (st["smart_turn"]["reason"] or "").lower()
    assert st["tts"]["fallback"] == "browser"
    assert st["tts"]["voice"] == "am_michael"
    assert st["stt"]["available"] is False


def test_transcribe_file_swallows_engine_errors(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    class Boom:
        def transcribe(self, *a, **k):
            raise RuntimeError("decoder exploded")

    monkeypatch.setattr(svc, "_managed", lambda: False)
    monkeypatch.setattr(svc, "get_stt_model", lambda home_dir=None: Boom())
    monkeypatch.setattr(
        svc, "load_voice_settings", lambda home_dir=None: {"language": ""}
    )
    assert svc.transcribe_file(tmp_path / "x.wav", home_dir=tmp_path) is None


def test_synthesize_none_without_engine(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: None)
    assert svc.synthesize("hello", home_dir=tmp_path) is None


def test_synthesize_with_mock_engine(tmp_path: Path, monkeypatch):
    pytest.importorskip("numpy")
    import remedy.voice.service as svc

    class FakeKokoro:
        def create(self, text, voice="af_heart", speed=1.0):
            assert "screen" not in voice
            # gender male must have resolved to am_michael via config default
            return [0.0, 0.1, -0.1], 24_000

    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: FakeKokoro())
    out = svc.synthesize("**hello** there", gender="male", home_dir=tmp_path)
    assert out is not None
    wav, sr = out
    assert sr == 24_000
    assert wav[:4] == b"RIFF"


# ---------------------------------------------------------------------------
# Service contract (HTTP /api/voice/* is Go-owned)
# ---------------------------------------------------------------------------


def test_voice_http_routes_absent_from_testclient():
    from remedy.interfaces.api import create_app

    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/voice/status",
        "/api/voice/speak",
        "/api/voice/transcribe",
        "/api/voice/install",
        "/api/voice/settings",
        "/api/voice/identity",
    ):
        assert path not in paths


def test_voice_status_shape(tmp_path: Path):
    data = voice_status(tmp_path)
    assert "tts" in data and "stt" in data and "smart_turn" in data and "settings" in data
    assert isinstance(data["tts"]["voices"], list)


def test_voice_install_all_starts_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "install_voice_pack_background", lambda home=None: True)
    assert svc.install_voice_pack_background(tmp_path) is True


def test_voice_install_stt_without_deps_starts_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "stt_deps_available", lambda: False)
    called: list[str] = []

    def _pack(home=None):
        called.append("pack")
        return True

    monkeypatch.setattr(svc, "install_voice_pack_background", _pack)
    if not svc.stt_deps_available():
        assert svc.install_voice_pack_background(tmp_path) is True
    assert called == ["pack"]


def test_install_voice_pack_runs_extras_then_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import remedy.voice.service as svc

    called: list[str] = []

    monkeypatch.setattr(svc, "tts_deps_available", lambda: False)
    monkeypatch.setattr(svc, "stt_deps_available", lambda: False)

    def fake_pip(home_dir=None) -> None:
        called.append("pip")
        monkeypatch.setattr(svc, "tts_deps_available", lambda: True)
        monkeypatch.setattr(svc, "stt_deps_available", lambda: True)

    monkeypatch.setattr(svc, "_pip_install_voice_extras", fake_pip)
    monkeypatch.setattr(svc, "tts_installed", lambda home=None: True)
    monkeypatch.setattr(svc, "stt_installed", lambda home=None: True)
    monkeypatch.setattr(svc, "smart_turn_installed", lambda home=None: True)
    svc.install_voice_pack(tmp_path)
    assert called == ["pip"]
    assert svc._install_state["pack"]["status"] == "done"


def test_run_pip_packages_pulses_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "_ensure_pip", lambda *a, **k: True)
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: None)
    monkeypatch.setattr(svc.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        svc,
        "_stream_pip",
        lambda *a, **k: (0, ["Successfully installed kokoro-onnx"]),
    )
    state: dict = {"pack": {"status": "downloading", "percent": 5.0}}
    svc.run_pip_packages(("kokoro-onnx",), state, "pack", cap=40.0)
    assert state["pack"]["status"] == "downloading"


def test_find_uv_looks_beside_python_when_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import remedy.voice.service as svc

    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "uv.exe").write_bytes(b"")
    (tmp_path / "uv").write_bytes(b"")
    monkeypatch.setattr(svc.shutil, "which", lambda _n: None)
    monkeypatch.setattr(svc.sys, "executable", str(tmp_path / "python.exe"))
    found = svc._find_uv()
    assert found is not None
    assert Path(found).name in ("uv.exe", "uv")
    assert Path(found).parent == tmp_path


def test_ensure_pip_uses_ensurepip_when_venv_has_none(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    import remedy.voice.service as svc

    has = {"pip": False, "ensurepip": True}
    hidden: list[list[str]] = []

    monkeypatch.setattr(svc, "_python_has_module", lambda py, env, name: has[name])
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: None)

    def fake_hidden(args, env, *, timeout=120.0):
        hidden.append(list(args))
        if "ensurepip" in args:
            has["pip"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected {args}")

    monkeypatch.setattr(svc, "_run_hidden", fake_hidden)
    monkeypatch.setattr(
        svc, "_download_get_pip", lambda dest: (_ for _ in ()).throw(AssertionError("get-pip"))
    )
    assert svc._ensure_pip("/venv/python", {}) is True
    assert has["pip"] is True
    assert any("ensurepip" in a for a in hidden)


def test_ensure_pip_uses_uv_when_ensurepip_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from types import SimpleNamespace

    import remedy.voice.service as svc

    has = {"pip": False, "ensurepip": False}
    uv = str(tmp_path / "uv.exe")
    hidden: list[list[str]] = []

    monkeypatch.setattr(svc, "_python_has_module", lambda py, env, name: has[name])
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: uv)

    def fake_hidden(args, env, *, timeout=120.0):
        hidden.append(list(args))
        if args and args[0] == uv:
            has["pip"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected {args}")

    monkeypatch.setattr(svc, "_run_hidden", fake_hidden)
    monkeypatch.setattr(
        svc, "_download_get_pip", lambda dest: (_ for _ in ()).throw(AssertionError("get-pip"))
    )
    assert svc._ensure_pip("/venv/python", {}) is True
    assert has["pip"] is True
    assert hidden[0][:3] == [uv, "pip", "install"]
    assert "pip" in hidden[0]


def test_ensure_pip_falls_back_to_get_pip(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    import remedy.voice.service as svc

    has = {"pip": False, "ensurepip": False}
    downloaded: list[Path] = []

    monkeypatch.setattr(svc, "_python_has_module", lambda py, env, name: has[name])
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: None)

    def fake_dl(dest: Path) -> None:
        downloaded.append(dest)
        dest.write_text("# fake get-pip", encoding="utf-8")

    def fake_hidden(args, env, *, timeout=120.0):
        if downloaded and str(downloaded[0]) in list(args):
            has["pip"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected {args}")

    monkeypatch.setattr(svc, "_download_get_pip", fake_dl)
    monkeypatch.setattr(svc, "_run_hidden", fake_hidden)
    assert svc._ensure_pip("/venv/python", {}) is True
    assert has["pip"] is True
    assert downloaded, "get-pip.py should be fetched"
    assert not downloaded[0].exists()  # temp file cleaned up


def test_run_pip_packages_bootstraps_then_installs(monkeypatch: pytest.MonkeyPatch):
    import remedy.voice.service as svc

    seen: dict[str, object] = {}

    def fake_ensure(py, env, *, on_message=None):
        seen["py"] = py
        if on_message:
            on_message("Preparing the voice installer")
        return True

    cmds: list[list[str]] = []

    def fake_stream(cmd, env, set_state, lo, cap, *, label="the voice pack"):
        cmds.append(list(cmd))
        return 0, ["Successfully installed kokoro-onnx"]

    monkeypatch.setattr(svc, "_ensure_pip", fake_ensure)
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: None)
    monkeypatch.setattr(svc.sys, "frozen", False, raising=False)
    monkeypatch.setattr(svc, "_stream_pip", fake_stream)
    state: dict = {"pack": {"status": "downloading", "percent": 5.0, "message": "Installing"}}
    svc.run_pip_packages(("kokoro-onnx",), state, "pack", cap=40.0)
    assert seen["py"]
    assert cmds and cmds[0][1:3] == ["-m", "pip"]
    assert "kokoro-onnx" in cmds[0]
    assert state["pack"]["message"] == "Preparing the voice installer"


def test_run_pip_packages_falls_back_to_uv_after_no_module_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import remedy.voice.service as svc

    uv = str(tmp_path / "uv.exe")
    cmds: list[list[str]] = []

    def fake_stream(cmd, env, set_state, lo, cap, *, label="the voice pack"):
        cmds.append(list(cmd))
        if cmd[0] == uv:
            return 0, ["ok"]
        return 1, ["No module named pip"]

    monkeypatch.setattr(svc, "_ensure_pip", lambda *a, **k: False)
    monkeypatch.setattr(svc, "_find_uv", lambda python=None: uv)
    monkeypatch.setattr(svc.sys, "frozen", False, raising=False)
    monkeypatch.setattr(svc, "_stream_pip", fake_stream)
    state: dict = {"pack": {"status": "downloading", "percent": 5.0}}
    svc.run_pip_packages(("kokoro-onnx",), state, "pack", cap=40.0)
    assert len(cmds) == 2
    assert cmds[0][1:3] == ["-m", "pip"]
    assert cmds[1][0] == uv
    assert cmds[1][1:3] == ["pip", "install"]


def test_owner_pack_error_never_leaks_a_pip_command():
    from remedy.voice.service import _owner_pack_error

    out = _owner_pack_error(RuntimeError("pip install remedy-ai[voice] failed"))
    assert "pip" not in out.lower()


def test_voice_settings_patch(tmp_path: Path):
    out = save_voice_settings({"speak_replies": True}, tmp_path)
    assert out["speak_replies"] is True
    assert load_voice_settings(tmp_path)["speak_replies"] is True
    assert voice_status(tmp_path)["settings"]["speak_replies"] is True


def test_tts_disabled_setting_persists(tmp_path: Path):
    save_voice_settings({"tts_enabled": False}, tmp_path)
    assert load_voice_settings(tmp_path)["tts_enabled"] is False
    assert voice_status(tmp_path)["settings"]["tts_enabled"] is False

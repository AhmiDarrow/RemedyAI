"""Checksummed first-use download of the HQ voice reference WAVs."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from remedy.voice import chatterbox as hq
from remedy.voice import reference_assets as refs


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Resp:
    def __init__(self, data: bytes, url: str = "https://example.test/file"):
        self._data = io.BytesIO(data)
        self._url = url

    def read(self, n: int = -1) -> bytes:
        return self._data.read(n)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _hide_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMEDY_VOICE_REFERENCE_SOURCE", raising=False)
    monkeypatch.setattr(refs, "checkout_reference_dir", lambda: None)


def test_pinned_hashes_match_assets() -> None:
    root = Path(__file__).resolve().parents[1] / "assets" / "voice-references"
    sums = (root / "SHA256SUMS.txt").read_text(encoding="utf-8")
    for key, expected in refs.REFERENCE_SHA256.items():
        assert expected in sums
        assert _sha((root / f"{key}.wav").read_bytes()) == expected


def test_package_does_not_ship_hq_wavs() -> None:
    packaged = Path(hq.__file__).resolve().parent / "references"
    assert list(packaged.glob("*.wav")) == []


def test_ensure_uses_checkout_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)

    def no_net(*args: object, **kwargs: object) -> None:
        raise AssertionError("checkout copy must not hit the network")

    monkeypatch.setattr(refs.urllib.request, "urlopen", no_net)
    path = refs.ensure_reference("female", tmp_path)
    assert path == tmp_path / "voice" / "references" / "female.wav"
    assert _sha(path.read_bytes()) == refs.REFERENCE_SHA256["female"]
    # Cache hit.
    again = refs.ensure_reference("female", tmp_path)
    assert again == path


def test_ensure_skips_network_under_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_checkout(monkeypatch)
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    with pytest.raises(refs.VoiceReferenceUnavailableError, match="disabled during tests"):
        refs.ensure_reference("female", tmp_path)


def test_ensure_downloads_verifies_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_checkout(monkeypatch)
    female = (
        Path(__file__).resolve().parents[1] / "assets" / "voice-references" / "female.wav"
    ).read_bytes()
    base = "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.67.0"

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("remedy-voice-female.wav"):
            return _Resp(female, f"{base}/remedy-voice-female.wav")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(refs.urllib.request, "urlopen", fake_open)
    path = refs.ensure_reference("female", tmp_path, version="0.67.0")
    assert path.is_file()
    assert _sha(path.read_bytes()) == refs.REFERENCE_SHA256["female"]

    def no_net(*args: object, **kwargs: object) -> None:
        raise AssertionError("cache hit must not hit the network")

    monkeypatch.setattr(refs.urllib.request, "urlopen", no_net)
    assert refs.ensure_reference("female", tmp_path, version="0.67.0") == path


def test_ensure_rejects_bad_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_checkout(monkeypatch)
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        return _Resp(b"not-a-real-wav" * 20, url)

    monkeypatch.setattr(refs.urllib.request, "urlopen", fake_open)
    with pytest.raises(refs.VoiceReferenceError, match="sha256"):
        refs.ensure_reference("female", tmp_path, version="0.67.0")
    dest = tmp_path / "voice" / "references" / "female.wav"
    assert not dest.is_file()
    assert not dest.with_suffix(".wav.part").is_file()


def test_failed_download_does_not_fall_back_to_kokoro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download that can be attempted must not silently use a Kokoro clip."""
    _hide_checkout(monkeypatch)
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")

    def boom(req, timeout=0):  # noqa: ANN001
        raise TimeoutError("simulated outage")

    monkeypatch.setattr(refs.urllib.request, "urlopen", boom)
    standin = hq._identity_dir(tmp_path) / "female.wav"
    standin.write_bytes(b"RIFF" + b"\x00" * 200)

    with pytest.raises(refs.VoiceReferenceError, match="Could not download"):
        hq.identity_prompt_path("female", tmp_path)

    def _must_not_bootstrap(*args: object, **kwargs: object) -> Path:
        raise AssertionError("Kokoro stand-in must not run when a download is possible")

    monkeypatch.setattr(hq, "_bootstrap_identity", _must_not_bootstrap)
    monkeypatch.setattr(hq, "get_chatterbox_engine", lambda home_dir=None: object())
    with pytest.raises(refs.VoiceReferenceError, match="Could not download"):
        hq.synthesize("hello", gender="female", home_dir=tmp_path)


def test_kokoro_standin_only_when_download_is_not_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_checkout(monkeypatch)
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    standin = hq._identity_dir(tmp_path) / "female.wav"
    standin.write_bytes(b"RIFF" + b"\x00" * 200)
    assert hq.identity_prompt_path("female", tmp_path) == standin

"""Wheel-pinned runtime checksums: the wheel, not the release, is the trust anchor."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from remedy.runtime import native_bootstrap as nb

RELEASES = "https://github.com/AhmiDarrow/RemedyAI/releases"
VER = "0.69.1"
RT = f"{RELEASES}/download/runtime-v{VER}"
GOOD_RUNTIME = b"genuine-runtime-0.69.1"
GOOD_CORE = b"genuine-core-0.69.1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Resp:
    def __init__(self, data: bytes, url: str):
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


def _not_found(url: str) -> HTTPError:
    return HTTPError(url, 404, "Not Found", hdrs=None, fp=io.BytesIO())  # type: ignore[arg-type]


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> nb.NativeAssets:
    monkeypatch.setattr(nb.platform, "system", lambda: "Linux")
    monkeypatch.setattr(nb.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.delenv("REMEDY_NATIVE_ALLOW_UNPINNED", raising=False)
    nb._verified_memo.clear()
    return nb._PLATFORM_ASSETS[("Linux", "x86_64")]


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch, linux: nb.NativeAssets) -> nb.NativePins:
    pins = nb.NativePins(
        version=VER,
        sha256={linux.runtime_asset: _sha(GOOD_RUNTIME), linux.core_asset: _sha(GOOD_CORE)},
    )
    monkeypatch.setattr(nb, "load_pins", lambda: pins)
    return pins


def _serve(monkeypatch: pytest.MonkeyPatch, routes: dict[str, bytes]) -> list[str]:
    requested: list[str] = []

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        requested.append(url)
        if "/latest" in url:
            raise AssertionError(f"pinned bootstrap must never consult Latest: {url}")
        if url in routes:
            return _Resp(routes[url], url)
        raise _not_found(url)

    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_open)
    return requested


def test_pinned_install_ignores_release_checksum_file(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = _serve(
        monkeypatch,
        {
            f"{RT}/{linux.runtime_asset}": GOOD_RUNTIME,
            f"{RT}/{linux.core_asset}": GOOD_CORE,
        },
    )
    installed = nb.ensure_native_runtime(tmp_path, version=VER)
    assert installed.version == VER
    assert installed.runtime_bin.read_bytes() == GOOD_RUNTIME
    assert not any(u.endswith(nb.SUMS_NAME) for u in requested)


def test_tampered_asset_is_refused(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = b"swapped-runtime"
    # The attacker also rewrote the release checksum file to match.
    requested = _serve(
        monkeypatch,
        {
            f"{RT}/{nb.SUMS_NAME}": f"{_sha(evil)}  {linux.runtime_asset}\n"
            f"{_sha(GOOD_CORE)}  {linux.core_asset}\n".encode(),
            f"{RT}/{linux.runtime_asset}": evil,
            f"{RT}/{linux.core_asset}": GOOD_CORE,
            f"{RELEASES}/download/v{VER}/{linux.runtime_asset}": evil,
        },
    )
    with pytest.raises(nb.NativeBootstrapError, match="did not match"):
        nb.ensure_native_runtime(tmp_path, version=VER)
    target = nb.install_dir(VER, linux.triple, home_dir=tmp_path)
    assert not (target / linux.runtime_local).exists()
    assert list(target.glob("*.part")) == []
    assert nb.cached_install(tmp_path, version=VER) is None
    # Wrong bytes at the expected place stop the search.
    assert f"{RELEASES}/download/v{VER}/{linux.runtime_asset}" not in requested


def test_tampered_sums_cannot_poison_a_pinned_install(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(
        monkeypatch,
        {
            f"{RT}/{nb.SUMS_NAME}": ("00" * 32 + f"  {linux.runtime_asset}\n").encode(),
            f"{RT}/{linux.runtime_asset}": GOOD_RUNTIME,
            f"{RT}/{linux.core_asset}": GOOD_CORE,
        },
    )
    installed = nb.ensure_native_runtime(tmp_path, version=VER)
    assert installed.core_lib.read_bytes() == GOOD_CORE


def test_missing_version_fails_closed(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = _serve(monkeypatch, {})
    with pytest.raises(nb.NativeBootstrapError, match="does not fall back"):
        nb.ensure_native_runtime(tmp_path, version=VER)
    assert requested and all(VER in u for u in requested)


def test_downgrade_attempt_is_refused(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_runtime, old_core = b"old-runtime-0.69.0", b"old-core-0.69.0"
    old = f"{RELEASES}/download/runtime-v0.69.0"
    requested = _serve(
        monkeypatch,
        {
            f"{old}/{nb.SUMS_NAME}": f"{_sha(old_runtime)}  {linux.runtime_asset}\n"
            f"{_sha(old_core)}  {linux.core_asset}\n".encode(),
            f"{old}/{linux.runtime_asset}": old_runtime,
            f"{old}/{linux.core_asset}": old_core,
        },
    )
    with pytest.raises(nb.NativeBootstrapError):
        nb.ensure_native_runtime(tmp_path, version=VER)
    assert not any("0.69.0" in u for u in requested)
    # An older version already sitting in the cache is not used either.
    old_dir = nb.install_dir("0.69.0", linux.triple, home_dir=tmp_path)
    old_dir.mkdir(parents=True)
    (old_dir / linux.runtime_local).write_bytes(old_runtime)
    (old_dir / linux.core_local).write_bytes(old_core)
    nb._write_marker(
        old_dir,
        {
            "ok": True,
            "version": "0.69.0",
            "triple": linux.triple,
            "runtime_sha256": _sha(old_runtime),
            "core_sha256": _sha(old_core),
        },
    )
    assert nb.cached_install(tmp_path, version=VER) is None
    with pytest.raises(nb.NativeBootstrapError):
        nb.ensure_native_runtime(tmp_path, version=VER)


def test_cache_not_matching_pins_is_redownloaded(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = b"planted-runtime"
    target = nb.install_dir(VER, linux.triple, home_dir=tmp_path)
    target.mkdir(parents=True)
    (target / linux.runtime_local).write_bytes(planted)
    (target / linux.core_local).write_bytes(GOOD_CORE)
    nb._write_marker(
        target,
        {
            "ok": True,
            "version": VER,
            "triple": linux.triple,
            "runtime_sha256": _sha(planted),
            "core_sha256": _sha(GOOD_CORE),
        },
    )
    assert nb.cached_install(tmp_path, version=VER) is None
    _serve(
        monkeypatch,
        {f"{RT}/{linux.runtime_asset}": GOOD_RUNTIME, f"{RT}/{linux.core_asset}": GOOD_CORE},
    )
    installed = nb.ensure_native_runtime(tmp_path, version=VER)
    assert installed.runtime_bin.read_bytes() == GOOD_RUNTIME


def test_pins_for_other_version_count_as_unpinned(
    tmp_path: Path, linux: nb.NativeAssets, pinned: nb.NativePins, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, {})
    with pytest.raises(nb.NativeBootstrapError, match="no pinned runtime checksums"):
        nb.ensure_native_runtime(tmp_path, version="0.69.2")


def test_parse_pins_rejects_bad_payloads() -> None:
    good = {"version": "v0.69.1", "sha256": {"remedy-runtime-x": "ab" * 32}}
    parsed = nb.parse_pins(good)
    assert parsed is not None and parsed.version == "0.69.1"
    assert nb.parse_pins({"version": "", "sha256": {}}) is None
    assert nb.parse_pins({"version": "0.69.1", "sha256": {"../x": "ab" * 32}}) is None
    assert nb.parse_pins({"version": "0.69.1", "sha256": {"x": "not-a-hash"}}) is None
    assert nb.parse_pins([]) is None


def test_source_tree_ships_unpinned() -> None:
    raw = json.loads(Path(nb.__file__).with_name(nb.PINS_NAME).read_text(encoding="utf-8"))
    assert nb.parse_pins(raw) is None, "do not commit filled-in pins; they are a build output"

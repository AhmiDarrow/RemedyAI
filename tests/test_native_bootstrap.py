"""First-run download of remedy-runtime + remedy_core from GitHub Releases."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from remedy.runtime import native_bootstrap as nb


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


def _sums(assets: nb.NativeAssets, runtime: bytes, core: bytes) -> bytes:
    text = (
        f"{_sha(runtime)}  {assets.runtime_asset}\n"
        f"{_sha(core)}  {assets.core_asset}\n"
    )
    return text.encode("utf-8")


@pytest.fixture
def linux_assets(monkeypatch: pytest.MonkeyPatch) -> nb.NativeAssets:
    assets = nb._PLATFORM_ASSETS[("Linux", "x86_64")]
    monkeypatch.setattr(nb.platform, "system", lambda: "Linux")
    monkeypatch.setattr(nb.platform, "machine", lambda: "x86_64")
    return assets


def test_unsupported_platform_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nb.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(nb.platform, "machine", lambda: "arm64")
    assert nb.assets_for_this_machine() is None
    reason = nb.unsupported_platform_reason()
    assert reason is not None
    assert "Darwin" in reason
    assert "go build" in reason


def test_parse_sha256sums_rejects_path_components() -> None:
    with pytest.raises(nb.NativeBootstrapError, match="path component"):
        nb.parse_sha256sums("abcd" * 16 + "  ../evil\n")


def test_parse_sha256sums_accepts_gnu_binary_marker() -> None:
    digest = "ab" * 32
    assert nb.parse_sha256sums(f"{digest} *remedy-runtime-x86_64-unknown-linux-gnu\n") == {
        "remedy-runtime-x86_64-unknown-linux-gnu": digest
    }


def test_cache_miss_without_network(tmp_path: Path, linux_assets: nb.NativeAssets) -> None:
    assert nb.cached_install(tmp_path, version="0.65.1") is None


def test_ensure_skips_network_under_pytest(
    tmp_path: Path, linux_assets: nb.NativeAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    with pytest.raises(nb.NativeBootstrapError, match="downloads are disabled"):
        nb.ensure_native_runtime(tmp_path, version="0.65.1")


def test_ensure_downloads_verifies_and_caches(
    tmp_path: Path, linux_assets: nb.NativeAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = b"runtime-bytes-v1"
    core = b"core-bytes-v1"
    sums = _sums(linux_assets, runtime, core)
    base = "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.65.1"

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith(nb.SUMS_NAME):
            return _Resp(sums, f"{base}/{nb.SUMS_NAME}")
        if url.endswith(linux_assets.runtime_asset):
            return _Resp(runtime, f"{base}/{linux_assets.runtime_asset}")
        if url.endswith(linux_assets.core_asset):
            return _Resp(core, f"{base}/{linux_assets.core_asset}")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_open)
    installed = nb.ensure_native_runtime(tmp_path, version="0.65.1")
    assert installed.version == "0.65.1"
    assert installed.runtime_bin.is_file()
    assert installed.core_lib.is_file()
    assert installed.runtime_bin.name == "remedy-runtime"
    assert installed.core_lib.name == "libremedy_core.so"
    assert _sha(installed.runtime_bin.read_bytes()) == _sha(runtime)

    # Second call is a cache hit — no network.
    def no_net(*args: object, **kwargs: object) -> None:
        raise AssertionError("cache hit must not hit the network")

    monkeypatch.setattr(nb.urllib.request, "urlopen", no_net)
    again = nb.ensure_native_runtime(tmp_path, version="0.65.1")
    assert again.runtime_bin == installed.runtime_bin


def test_tampered_download_is_rejected(
    tmp_path: Path, linux_assets: nb.NativeAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = b"good-runtime"
    core = b"good-core"
    sums = _sums(linux_assets, runtime, core)
    base = "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.65.1"

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith(nb.SUMS_NAME):
            return _Resp(sums, f"{base}/{nb.SUMS_NAME}")
        if url.endswith(linux_assets.runtime_asset):
            return _Resp(b"TAMPERED", f"{base}/{linux_assets.runtime_asset}")
        if url.endswith(linux_assets.core_asset):
            return _Resp(core, f"{base}/{linux_assets.core_asset}")
        raise AssertionError(url)

    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_open)
    with pytest.raises(nb.NativeBootstrapError, match="sha256"):
        nb.ensure_native_runtime(tmp_path, version="0.65.1")
    target = nb.install_dir("0.65.1", linux_assets.triple, home_dir=tmp_path)
    assert not (target / linux_assets.runtime_local).is_file()
    assert list(target.glob("*.part")) == []


def test_missing_sums_falls_back_to_latest_then_caches_real_tag(
    tmp_path: Path, linux_assets: nb.NativeAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = b"latest-runtime"
    core = b"latest-core"
    sums = _sums(linux_assets, runtime, core)
    latest_base = "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.66.0"

    def fake_open(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/download/v0.65.1/" in url:
            raise HTTPError(url, 404, "Not Found", hdrs=None, fp=io.BytesIO())  # type: ignore[arg-type]
        if url.endswith(nb.SUMS_NAME):
            return _Resp(sums, f"{latest_base}/{nb.SUMS_NAME}")
        if url.endswith(linux_assets.runtime_asset):
            return _Resp(runtime, f"{latest_base}/{linux_assets.runtime_asset}")
        if url.endswith(linux_assets.core_asset):
            return _Resp(core, f"{latest_base}/{linux_assets.core_asset}")
        raise AssertionError(url)

    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_open)
    installed = nb.ensure_native_runtime(tmp_path, version="0.65.1")
    assert installed.version == "0.66.0"
    assert installed.runtime_bin.parent.name == linux_assets.triple
    assert "0.66.0" in str(installed.runtime_bin)


def test_checksum_file_without_this_platform_fails_clearly(
    tmp_path: Path, linux_assets: nb.NativeAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = ("ab" * 32 + "  remedy-runtime-x86_64-pc-windows-msvc.exe\n").encode()
    base = "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.65.1"

    def fake_open(req, timeout=0):  # noqa: ANN001
        return _Resp(body, f"{base}/{nb.SUMS_NAME}")

    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_open)
    with pytest.raises(nb.NativeBootstrapError, match="no assets for"):
        nb.ensure_native_runtime(tmp_path, version="0.65.1")


def test_tag_from_final_url() -> None:
    assert (
        nb._tag_from_final_url(
            "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.65.1/"
            + nb.SUMS_NAME,
            "fallback",
        )
        == "0.65.1"
    )
    assert (
        nb._tag_from_final_url(
            "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/"
            + nb.SUMS_NAME,
            "fallback",
        )
        == "fallback"
    )

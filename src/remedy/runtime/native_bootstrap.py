"""Download and cache Go ``remedy-runtime`` + Zig ``remedy_core``.

Packaged Desktop already bundles these binaries. The PyPI CLI does not, so
``remedy serve`` calls :func:`ensure_native_runtime` on first use: download the
platform assets for this Remedy version (falling back to GitHub's Latest
release when this tag has no native assets yet), verify sha256 against
``remedy-native-SHA256SUMS.txt``, and cache them under
``~/.remedy/runtime/<version>/<triple>/``.

Same shape as the managed Claimidx wheel and the voice CPython: checksum
pins, atomic replace, a failed download never leaves a half-file that later
looks valid. Network is skipped during pytest unless ``REMEDY_ENSURE_ASSETS=1``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import stat
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from remedy.home import default_home

logger = logging.getLogger(__name__)

SUMS_NAME = "remedy-native-SHA256SUMS.txt"
DEFAULT_RELEASE_BASE = "https://github.com/AhmiDarrow/RemedyAI/releases"
USER_AGENT = "RemedyAI-NativeBootstrap/1.0"
# Go/Zig binaries are small; refuse anything that would fill a disk.
MAX_FILE_BYTES = 80 * 1024 * 1024
_MARKER = "runtime.json"
_lock = threading.Lock()


class NativeBootstrapError(RuntimeError):
    """Prebuilt runtime could not be downloaded, verified, or cached."""


@dataclass(frozen=True, slots=True)
class NativeAssets:
    """Filenames for one GitHub-release platform slice."""

    triple: str
    runtime_asset: str
    core_asset: str
    runtime_local: str
    core_local: str


@dataclass(frozen=True, slots=True)
class NativeInstall:
    """Verified runtime + core on disk."""

    version: str
    triple: str
    runtime_bin: Path
    core_lib: Path
    source: str


_PLATFORM_ASSETS: dict[tuple[str, str], NativeAssets] = {
    ("Windows", "AMD64"): NativeAssets(
        "x86_64-pc-windows-msvc",
        "remedy-runtime-x86_64-pc-windows-msvc.exe",
        "remedy_core-x86_64-pc-windows-msvc.dll",
        "remedy-runtime.exe",
        "remedy_core.dll",
    ),
    ("Windows", "x86_64"): NativeAssets(
        "x86_64-pc-windows-msvc",
        "remedy-runtime-x86_64-pc-windows-msvc.exe",
        "remedy_core-x86_64-pc-windows-msvc.dll",
        "remedy-runtime.exe",
        "remedy_core.dll",
    ),
    ("Linux", "x86_64"): NativeAssets(
        "x86_64-unknown-linux-gnu",
        "remedy-runtime-x86_64-unknown-linux-gnu",
        "libremedy_core-x86_64-unknown-linux-gnu.so",
        "remedy-runtime",
        "libremedy_core.so",
    ),
    ("Linux", "AMD64"): NativeAssets(
        "x86_64-unknown-linux-gnu",
        "remedy-runtime-x86_64-unknown-linux-gnu",
        "libremedy_core-x86_64-unknown-linux-gnu.so",
        "remedy-runtime",
        "libremedy_core.so",
    ),
}


def assets_for_this_machine() -> NativeAssets | None:
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key == ("Linux", "arm64"):
        key = ("Linux", "aarch64")
    return _PLATFORM_ASSETS.get(key)


def unsupported_platform_reason() -> str | None:
    if assets_for_this_machine() is not None:
        return None
    return (
        f"No prebuilt remedy-runtime is published for this computer yet "
        f"({platform.system()} {platform.machine()}). Build from a checkout: "
        "cd native/go && go build -o ../../desktop/bin/remedy-runtime ./cmd/remedy-runtime "
        "&& cd ../zig && zig build -Doptimize=ReleaseSafe, then put desktop/bin on PATH "
        "or set REMEDY_NATIVE_RUNTIME_BIN."
    )


def package_version() -> str:
    from remedy import __version__

    return str(__version__ or "").strip() or "0.0.0"


def _home(home_dir: Path | str | None = None) -> Path:
    if home_dir is not None:
        return Path(home_dir).expanduser()
    return default_home()


def cache_root(home_dir: Path | str | None = None) -> Path:
    return _home(home_dir) / "runtime"


def install_dir(
    version: str,
    triple: str,
    *,
    home_dir: Path | str | None = None,
) -> Path:
    return cache_root(home_dir) / version / triple


def release_base() -> str:
    override = (os.environ.get("REMEDY_NATIVE_RELEASE_BASE") or "").strip()
    return override.rstrip("/") if override else DEFAULT_RELEASE_BASE


def _skip_network() -> bool:
    if os.environ.get("REMEDY_NATIVE_BOOTSTRAP_DISABLE") == "1":
        return True
    if os.environ.get("REMEDY_ENSURE_ASSETS") == "1":
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _marker_path(directory: Path) -> Path:
    return directory / _MARKER


def _read_marker(directory: Path) -> dict[str, Any]:
    try:
        raw = json.loads(_marker_path(directory).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_marker(directory: Path, data: dict[str, Any]) -> None:
    from remedy.core.atomic_json import write_text_atomic

    directory.mkdir(parents=True, exist_ok=True)
    write_text_atomic(_marker_path(directory), json.dumps(data, indent=2) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse GNU ``sha256sum`` output; reject odd paths."""
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0].lower(), parts[-1]
        name = name.lstrip("*")
        if len(digest) != 64:
            continue
        try:
            int(digest, 16)
        except ValueError:
            continue
        if "/" in name or "\\" in name or name in (".", "..") or ".." in name:
            raise NativeBootstrapError(
                f"refusing checksum entry with a path component: {name!r}"
            )
        found[name] = digest
    return found


def _verified_install(directory: Path, assets: NativeAssets, version: str) -> NativeInstall | None:
    runtime = directory / assets.runtime_local
    core = directory / assets.core_local
    marker = _read_marker(directory)
    if not runtime.is_file() or not core.is_file():
        return None
    if not marker.get("ok"):
        return None
    if marker.get("version") != version or marker.get("triple") != assets.triple:
        return None
    expected_runtime = marker.get("runtime_sha256")
    expected_core = marker.get("core_sha256")
    if not isinstance(expected_runtime, str) or not isinstance(expected_core, str):
        return None
    try:
        if _sha256_file(runtime) != expected_runtime or _sha256_file(core) != expected_core:
            return None
    except OSError:
        return None
    return NativeInstall(
        version=version,
        triple=assets.triple,
        runtime_bin=runtime,
        core_lib=core,
        source=str(marker.get("source") or "cache"),
    )


def cached_install(
    home_dir: Path | str | None = None,
    *,
    version: str | None = None,
) -> NativeInstall | None:
    """Return a checksum-verified cache hit, or None. No network."""
    assets = assets_for_this_machine()
    if assets is None:
        return None
    ver = version or package_version()
    return _verified_install(install_dir(ver, assets.triple, home_dir=home_dir), assets, ver)


def _urlopen(url: str, *, timeout: float = 60.0):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def _download_bytes(url: str, *, max_bytes: int, timeout: float = 60.0) -> tuple[bytes, str]:
    try:
        with _urlopen(url, timeout=timeout) as response:
            final_url = str(getattr(response, "geturl", lambda: url)())
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise NativeBootstrapError(
                        f"{url} was larger than the {max_bytes} byte limit"
                    )
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        raise NativeBootstrapError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NativeBootstrapError(f"could not fetch {url}: {exc}") from exc
    return b"".join(chunks), final_url


def _download_file(url: str, dest: Path, *, expected_sha256: str, timeout: float = 120.0) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with _urlopen(url, timeout=timeout) as response, tmp.open("wb") as out:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise NativeBootstrapError(
                        f"{url} was larger than the {MAX_FILE_BYTES} byte limit"
                    )
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise NativeBootstrapError(
                f"{dest.name}: sha256 {actual} did not match {expected_sha256}"
            )
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _tag_from_final_url(final_url: str, fallback: str) -> str:
    path = unquote(urlparse(final_url).path)
    parts = path.split("/")
    try:
        idx = parts.index("download")
        tag = parts[idx + 1] if idx + 1 < len(parts) else ""
    except ValueError:
        tag = ""
    tag = tag.strip()
    if tag.startswith("v") and tag[1:].replace(".", "", 2).replace("-", "").isalnum():
        return tag[1:] if tag.startswith("v") else tag
    # latest/download/... has no tag in the pre-redirect URL.
    return fallback


def _sums_candidates(version: str) -> list[tuple[str, str]]:
    """(label, url) pairs: exact tag first, then GitHub Latest."""
    base = release_base()
    tag = version if version.startswith("v") else f"v{version}"
    return [
        (tag.lstrip("v"), f"{base}/download/{tag}/{SUMS_NAME}"),
        ("latest", f"{base}/latest/download/{SUMS_NAME}"),
    ]


def _fetch_sums(version: str) -> tuple[str, dict[str, str], str]:
    errors: list[str] = []
    seen_urls: set[str] = set()
    for label, url in _sums_candidates(version):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            body, final_url = _download_bytes(url, max_bytes=64 * 1024)
        except NativeBootstrapError as exc:
            errors.append(str(exc))
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeError as exc:
            raise NativeBootstrapError(f"{SUMS_NAME} was not UTF-8: {exc}") from exc
        sums = parse_sha256sums(text)
        if not sums:
            errors.append(f"{url}: no checksum lines")
            continue
        resolved = _tag_from_final_url(final_url, label)
        return resolved, sums, final_url.rsplit("/", 1)[0]
    detail = "; ".join(errors) if errors else "no checksum file URLs to try"
    raise NativeBootstrapError(
        f"No {SUMS_NAME} on the v{version} GitHub release (or Latest). {detail}. "
        "The next Remedy release must attach standalone remedy-runtime / "
        "remedy_core assets plus this checksum file. Until then, build from "
        "a checkout (see the install manual) or use the Desktop installer."
    )


def _make_executable(path: Path) -> None:
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stage_install(
    *,
    home_dir: Path | str | None,
    assets: NativeAssets,
    version: str,
    sums: dict[str, str],
    asset_base: str,
) -> NativeInstall:
    runtime_sha = sums.get(assets.runtime_asset)
    core_sha = sums.get(assets.core_asset)
    if not runtime_sha or not core_sha:
        have = ", ".join(sorted(sums)) or "(empty)"
        raise NativeBootstrapError(
            f"The checksum file has no assets for {assets.triple}. "
            f"Need {assets.runtime_asset} and {assets.core_asset}. Found: {have}. "
            "Build from a checkout or install Desktop from GitHub Releases."
        )
    target = install_dir(version, assets.triple, home_dir=home_dir)
    target.mkdir(parents=True, exist_ok=True)
    runtime_dest = target / assets.runtime_local
    core_dest = target / assets.core_local
    _download_file(f"{asset_base}/{assets.runtime_asset}", runtime_dest, expected_sha256=runtime_sha)
    _download_file(f"{asset_base}/{assets.core_asset}", core_dest, expected_sha256=core_sha)
    _make_executable(runtime_dest)
    if sys.platform != "win32":
        # Shared libraries sometimes need +x to dlopen on strict systems.
        _make_executable(core_dest)
    _write_marker(
        target,
        {
            "ok": True,
            "version": version,
            "triple": assets.triple,
            "runtime_sha256": runtime_sha,
            "core_sha256": core_sha,
            "source": asset_base,
        },
    )
    installed = _verified_install(target, assets, version)
    if installed is None:
        raise NativeBootstrapError("cached runtime failed verification after download")
    return installed


def ensure_native_runtime(
    home_dir: Path | str | None = None,
    *,
    version: str | None = None,
) -> NativeInstall:
    """Return a verified install, downloading once if the cache is cold.

    Raises :class:`NativeBootstrapError` when this machine has no prebuilt,
    network is skipped, or verification fails.
    """
    assets = assets_for_this_machine()
    if assets is None:
        raise NativeBootstrapError(unsupported_platform_reason() or "unsupported platform")
    ver = version or package_version()
    with _lock:
        hit = cached_install(home_dir, version=ver)
        if hit is not None:
            return hit
        # A Latest fallback may have already been cached under its real tag.
        if _skip_network():
            raise NativeBootstrapError(
                "remedy-runtime is not cached and downloads are disabled "
                "(tests, or REMEDY_NATIVE_BOOTSTRAP_DISABLE=1)."
            )
        resolved_version, sums, asset_base = _fetch_sums(ver)
        # If Latest resolved to another tag, use that cache slot (and retry a hit).
        if resolved_version != ver:
            existing = cached_install(home_dir, version=resolved_version)
            if existing is not None:
                return existing
        return _stage_install(
            home_dir=home_dir,
            assets=assets,
            version=resolved_version,
            sums=sums,
            asset_base=asset_base,
        )


def cached_runtime_bin(home_dir: Path | str | None = None) -> Path | None:
    hit = cached_install(home_dir)
    return hit.runtime_bin if hit is not None else None


def cached_core_lib(home_dir: Path | str | None = None) -> Path | None:
    hit = cached_install(home_dir)
    return hit.core_lib if hit is not None else None

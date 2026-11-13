"""Checksummed first-use download of the HQ (Chatterbox) voice references.

The two public-domain LibriVox clips are not in the PyPI wheel. First HQ use
downloads ``remedy-voice-{gender}.wav`` from the GitHub release, verifies a
pin compiled into this module, and caches the file under
``~/.remedy/voice/references/``. A checkout can use ``assets/voice-references/``
without a network hop. When a download can be attempted, a failure is raised
rather than silently falling back to a Kokoro stand-in.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from remedy.home import default_home

logger = logging.getLogger(__name__)

USER_AGENT = "RemedyAI-VoiceReference/1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024

# Pins for the LibriVox clips in assets/voice-references/. A release asset
# that does not match is rejected even if GitHub's SUMS file agrees with it.
REFERENCE_SHA256 = {
    "female": "fee8d3d4c2c55a64839ba862fcbf675707a1b2000aaa7dbff243dc96211ee5a6",
    "male": "b6faf84951c98f1c52b8e8a113140e43c4ca877d7efba43fab24fb264132966a",
}
RELEASE_ASSET = {
    "female": "remedy-voice-female.wav",
    "male": "remedy-voice-male.wav",
}


class VoiceReferenceError(RuntimeError):
    """HQ reference could not be downloaded, verified, or cached."""


class VoiceReferenceUnavailableError(VoiceReferenceError):
    """No cache, and a download is not possible (tests or explicit disable)."""


def _home(home_dir: Path | str | None = None) -> Path:
    if home_dir is not None:
        return Path(home_dir).expanduser()
    return default_home()


def cache_dir(home_dir: Path | str | None = None) -> Path:
    return _home(home_dir) / "voice" / "references"


def normalize_reference_gender(gender: str | None) -> str:
    try:
        from remedy.core.agent_identity import normalize_agent_gender

        g = normalize_agent_gender(gender)
    except Exception:
        g = (gender or "female").strip().lower() or "female"
    if g == "neutral":
        return "female"
    if g in REFERENCE_SHA256:
        return g
    return "female"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_file(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size <= 64:
            return False
        return _sha256_file(path) == expected.lower()
    except OSError:
        return False


def _skip_network() -> bool:
    if os.environ.get("REMEDY_NATIVE_BOOTSTRAP_DISABLE") == "1":
        return True
    if os.environ.get("REMEDY_ENSURE_ASSETS") == "1":
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _download_possible() -> bool:
    """True when we would attempt a network fetch (not tests / not disabled)."""
    return not _skip_network()


def checkout_reference_dir() -> Path | None:
    override = (os.environ.get("REMEDY_VOICE_REFERENCE_SOURCE") or "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None
    try:
        root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    candidate = root / "assets" / "voice-references"
    if (candidate / "female.wav").is_file() and (candidate / "male.wav").is_file():
        return candidate
    return None


def cached_reference(gender: str | None, home_dir: Path | str | None = None) -> Path | None:
    """Return the verified cache file, or None if missing or the hash is wrong."""
    key = normalize_reference_gender(gender)
    path = cache_dir(home_dir) / f"{key}.wav"
    if _verified_file(path, REFERENCE_SHA256[key]):
        return path
    return None


def local_reference(gender: str | None, home_dir: Path | str | None = None) -> Path | None:
    """Verified cache or checkout copy, without downloading."""
    cached = cached_reference(gender, home_dir)
    if cached is not None:
        return cached
    source = checkout_reference_dir()
    if source is None:
        return None
    key = normalize_reference_gender(gender)
    path = source / f"{key}.wav"
    if _verified_file(path, REFERENCE_SHA256[key]):
        return path
    return None


def _install_from_path(src: Path, dest: Path, expected: str) -> Path:
    if not _verified_file(src, expected):
        raise VoiceReferenceError(f"{src.name}: sha256 did not match the pinned HQ voice reference")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(src.read_bytes())
        if _sha256_file(tmp) != expected.lower():
            raise VoiceReferenceError(
                f"{dest.name}: sha256 did not match the pinned HQ voice reference"
            )
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def _urlopen(url: str, *, timeout: float = 60.0):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def _download_file(url: str, dest: Path, *, expected_sha256: str, timeout: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
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
                    raise VoiceReferenceError(
                        f"{url} was larger than the {MAX_FILE_BYTES} byte limit"
                    )
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise VoiceReferenceError(
                f"{dest.name}: sha256 {actual} did not match {expected_sha256}"
            )
        tmp.replace(dest)
    except VoiceReferenceError:
        tmp.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise VoiceReferenceError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise VoiceReferenceError(f"could not fetch {url}: {exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _release_base() -> str:
    from remedy.runtime.native_bootstrap import release_base

    return release_base()


def _download_candidates(version: str, asset: str) -> list[str]:
    base = _release_base()
    tag = version if version.startswith("v") else f"v{version}"
    urls = [f"{base}/download/{tag}/{asset}"]
    latest = f"{base}/latest/download/{asset}"
    if latest not in urls:
        urls.append(latest)
    return urls


def ensure_reference(
    gender: str | None,
    home_dir: Path | str | None = None,
    *,
    version: str | None = None,
) -> Path:
    """Return a verified HQ reference, downloading or copying it if needed.

    Raises :class:`VoiceReferenceUnavailableError` when no cache exists and a
    download is not possible. Raises :class:`VoiceReferenceError` when a
    download was attempted (or required) and failed. Never returns an
    unverified file.
    """
    key = normalize_reference_gender(gender)
    expected = REFERENCE_SHA256[key]
    cached = cached_reference(key, home_dir)
    if cached is not None:
        return cached

    dest = cache_dir(home_dir) / f"{key}.wav"
    source_dir = checkout_reference_dir()
    if source_dir is not None:
        src = source_dir / f"{key}.wav"
        if src.is_file():
            return _install_from_path(src, dest, expected)

    if _skip_network():
        raise VoiceReferenceUnavailableError(
            "HQ voice reference downloads are disabled during tests "
            "(set REMEDY_ENSURE_ASSETS=1 to allow them)"
        )

    from remedy.runtime.native_bootstrap import package_version

    ver = (version or package_version()).strip() or package_version()
    asset = RELEASE_ASSET[key]
    errors: list[str] = []
    for url in _download_candidates(ver, asset):
        try:
            _download_file(url, dest, expected_sha256=expected)
            logger.info("voice: cached HQ reference %s from %s", dest.name, url)
            return dest
        except VoiceReferenceError as exc:
            errors.append(str(exc))
            continue
    detail = "; ".join(errors) if errors else "no URLs to try"
    raise VoiceReferenceError(
        f"Could not download the {key} HQ voice reference for v{ver}. {detail}. "
        "Chatterbox will not fall back to a Kokoro stand-in when a download "
        "can be attempted."
    )

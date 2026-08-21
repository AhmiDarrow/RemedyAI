"""Locate and optionally install a ripgrep (rg) binary for repo_search.

Resolution order (find_rg):
  1. Bundled / user-home install (~/.remedy/bin, package resources)
  2. System PATH (rg / ripgrep)

License: ripgrep is dual-licensed MIT OR Unlicense (BurntSushi) — free to
redistribute. Official release assets are pinned with SHA-256 checksums.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from remedy.home import default_home

logger = logging.getLogger(__name__)

# Pinned upstream release (bump deliberately).
RG_VERSION = "15.2.0"
RG_BASE_URL = f"https://github.com/BurntSushi/ripgrep/releases/download/{RG_VERSION}"

# asset_name -> sha256 of the archive (from official .sha256 release assets).
_RG_ASSETS: dict[str, str] = {
    "ripgrep-15.2.0-x86_64-pc-windows-msvc.zip": (
        "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5"
    ),
    "ripgrep-15.2.0-aarch64-pc-windows-msvc.zip": (
        "e4abca10c3a64ebea742667dd7009449d49403db5460dd6873e389fa2945360f"
    ),
    "ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz": (
        "33e15bcf1624b25cdd2a55813a47a2f95dbe126268203e76aa6a585d1e7b149c"
    ),
    "ripgrep-15.2.0-aarch64-unknown-linux-musl.tar.gz": (
        "800b1e7206afe799dfb5a6901f23147cfaabe0e52210538100f61e86e1740915"
    ),
    "ripgrep-15.2.0-x86_64-apple-darwin.tar.gz": (
        "af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1"
    ),
    "ripgrep-15.2.0-aarch64-apple-darwin.tar.gz": (
        "3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4"
    ),
}


def _rg_exe_name() -> str:
    return "rg.exe" if sys.platform == "win32" else "rg"


def _home_bin_dir(home_dir: str | Path | None = None) -> Path:
    if home_dir is not None:
        base = Path(home_dir).expanduser()
    else:
        env = (os.environ.get("REMEDY_HOME") or "").strip()
        base = Path(env).expanduser() if env else default_home()
    return base / "bin"


def _package_resource_bins() -> list[Path]:
    """Directories that may contain a prebundled rg next to the package / app."""
    dirs: list[Path] = []
    try:
        import remedy

        pkg = Path(remedy.__file__).resolve().parent
        dirs.append(pkg / "bundled" / "bin")
        dirs.append(pkg / "bundled" / "rg")
    except Exception:
        pass
    # Frozen / desktop sidecar: next to executable
    try:
        if getattr(sys, "frozen", False):
            dirs.append(Path(sys.executable).resolve().parent)
            dirs.append(Path(sys.executable).resolve().parent / "bin")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                dirs.append(Path(meipass) / "bin")
    except Exception:
        pass
    # Repo layout during development
    try:
        here = Path(__file__).resolve()
        # src/remedy/core/rg_binary.py → repo root
        repo = here.parents[3]
        dirs.append(repo / "third_party" / "ripgrep" / "bin")
        dirs.append(repo / "desktop" / "bin")
    except Exception:
        pass
    return dirs


def _candidate_binaries(home_dir: str | Path | None = None) -> list[Path]:
    name = _rg_exe_name()
    out: list[Path] = []
    out.append(_home_bin_dir(home_dir) / name)
    for d in _package_resource_bins():
        out.append(d / name)
    return out


def find_bundled_rg(home_dir: str | Path | None = None) -> Path | None:
    """Return path to Remedy-owned rg if present and executable."""
    for cand in _candidate_binaries(home_dir):
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def find_system_rg() -> Path | None:
    which = shutil.which("rg") or shutil.which("ripgrep")
    if which:
        return Path(which)
    return None


def find_rg(
    home_dir: str | Path | None = None,
    *,
    prefer_system: bool = False,
) -> tuple[Path | None, str]:
    """Locate rg. Returns (path, source) where source is bundled|system|none.

    Default prefers bundled/home install over PATH so product behavior is stable.
    """
    if prefer_system:
        sys_rg = find_system_rg()
        if sys_rg is not None:
            return sys_rg, "system"
        bundled = find_bundled_rg(home_dir)
        if bundled is not None:
            return bundled, "bundled"
        return None, "none"

    bundled = find_bundled_rg(home_dir)
    if bundled is not None:
        return bundled, "bundled"
    sys_rg = find_system_rg()
    if sys_rg is not None:
        return sys_rg, "system"
    return None, "none"


def engine_label(source: str) -> str:
    if source == "bundled":
        return "bundled-rg"
    if source == "system":
        return "rg"
    return "python"


def _platform_asset() -> tuple[str, str] | None:
    """Return (asset_name, sha256) for this platform, or None if unsupported."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    is_x64 = machine in ("x86_64", "amd64", "x64")

    if system == "windows":
        if is_arm:
            name = f"ripgrep-{RG_VERSION}-aarch64-pc-windows-msvc.zip"
        elif is_x64:
            name = f"ripgrep-{RG_VERSION}-x86_64-pc-windows-msvc.zip"
        else:
            return None
    elif system == "darwin":
        if is_arm:
            name = f"ripgrep-{RG_VERSION}-aarch64-apple-darwin.tar.gz"
        elif is_x64:
            name = f"ripgrep-{RG_VERSION}-x86_64-apple-darwin.tar.gz"
        else:
            return None
    elif system == "linux":
        # musl static builds run on most distros
        if is_arm:
            name = f"ripgrep-{RG_VERSION}-aarch64-unknown-linux-musl.tar.gz"
        elif is_x64:
            name = f"ripgrep-{RG_VERSION}-x86_64-unknown-linux-musl.tar.gz"
        else:
            return None
    else:
        return None

    digest = _RG_ASSETS.get(name)
    if not digest:
        return None
    return name, digest


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_rg_from_archive(archive: Path, dest_dir: Path) -> Path:
    """Extract rg binary from zip/tar.gz into dest_dir; return path to binary."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_name = _rg_exe_name()
    found: Path | None = None

    if archive.suffix == ".zip" or archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                base = Path(info.filename).name
                if base == exe_name or base == "rg":
                    target = dest_dir / exe_name
                    with zf.open(info, "r") as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    found = target
                    break
    else:
        with tarfile.open(archive, "r:*") as tf:
            for member in tf.getmembers():
                base = Path(member.name).name
                if base in ("rg", exe_name) and member.isfile():
                    target = dest_dir / exe_name
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    with extracted, target.open("wb") as out:
                        shutil.copyfileobj(extracted, out)
                    found = target
                    break

    if found is None:
        raise FileNotFoundError(f"rg binary not found inside {archive.name}")
    from contextlib import suppress

    with suppress(OSError):
        found.chmod(found.stat().st_mode | 0o111)
    return found


_ensure_lock = threading.Lock()
_ensure_started = False
_ensure_failed = False


def schedule_ensure_rg(home_dir: str | Path | None = None) -> None:
    """Start a background download of rg once per process (non-blocking)."""
    global _ensure_started, _ensure_failed
    path, _src = find_rg(home_dir)
    if path is not None:
        return
    with _ensure_lock:
        if _ensure_started or _ensure_failed:
            return
        _ensure_started = True

    def _worker() -> None:
        global _ensure_failed
        try:
            info = ensure_rg(home_dir, download=True)
            if not info.get("ok"):
                _ensure_failed = True
                logger.warning("background ensure_rg failed: %s", info.get("error"))
            else:
                logger.info("background ensure_rg installed %s", info.get("path"))
        except Exception as e:
            _ensure_failed = True
            logger.warning("background ensure_rg exception: %s", e)

    import threading

    threading.Thread(target=_worker, name="remedy-ensure-rg", daemon=True).start()


def ensure_rg(
    home_dir: str | Path | None = None,
    *,
    download: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Ensure rg is available; optionally download the pinned release.

    Returns dict: ok, path, source, error?, version.
    Prefer :func:`schedule_ensure_rg` on the hot path so chat is not blocked.
    """
    global _ensure_failed
    path, source = find_rg(home_dir)
    if path is not None:
        return {
            "ok": True,
            "path": str(path),
            "source": source,
            "version": RG_VERSION,
            "engine": engine_label(source),
        }

    if not download or _ensure_failed:
        return {
            "ok": False,
            "path": None,
            "source": "none",
            "version": RG_VERSION,
            "engine": "python",
            "error": (
                "rg not found (download disabled)"
                if not download
                else "rg not found (prior download failed)"
            ),
        }

    asset = _platform_asset()
    if asset is None:
        return {
            "ok": False,
            "path": None,
            "source": "none",
            "version": RG_VERSION,
            "engine": "python",
            "error": f"no pinned rg build for {platform.system()} {platform.machine()}",
        }

    asset_name, expected = asset
    url = f"{RG_BASE_URL}/{asset_name}"
    dest_dir = _home_bin_dir(home_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-rg-ensure/1.0"})
        with tempfile.TemporaryDirectory(prefix="remedy-rg-") as tmp:
            tmp_path = Path(tmp) / asset_name
            with urlopen(req, timeout=timeout) as resp, tmp_path.open("wb") as out:
                shutil.copyfileobj(resp, out)
            digest = _sha256_file(tmp_path)
            if digest.lower() != expected.lower():
                return {
                    "ok": False,
                    "path": None,
                    "source": "none",
                    "version": RG_VERSION,
                    "engine": "python",
                    "error": (
                        f"checksum mismatch for {asset_name}: "
                        f"got {digest}, expected {expected}"
                    ),
                }
            binary = _extract_rg_from_archive(tmp_path, dest_dir)
        return {
            "ok": True,
            "path": str(binary.resolve()),
            "source": "bundled",
            "version": RG_VERSION,
            "engine": "bundled-rg",
            "downloaded": True,
        }
    except Exception as e:
        logger.warning("ensure_rg download failed: %s", e)
        return {
            "ok": False,
            "path": None,
            "source": "none",
            "version": RG_VERSION,
            "engine": "python",
            "error": str(e),
        }

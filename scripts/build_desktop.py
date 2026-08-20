"""PyInstaller build script for Remedy Desktop.

Creates a standalone Windows .exe for the remedy CLI server,
suitable for bundling as a Tauri sidecar.

Usage:
    python scripts/build_desktop.py          # build standalone exe
    python scripts/build_desktop.py --clean  # clean build from scratch
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_BIN = ROOT / "desktop" / "bin"
DIST_DIR = ROOT / "dist"
NSIS_DIR = (
    ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
)


# Heavy optional packages that must never be frozen into the sidecar.
SIDECAR_EXCLUDES = (
    "torch", "torchvision", "torchaudio", "functorch",
    "chatterbox", "transformers", "tokenizers", "safetensors",
    "faster_whisper", "ctranslate2", "kokoro_onnx", "onnxruntime",
    "espeakng_loader", "phonemizer",
    "cupy", "cv2", "scipy", "sklearn", "pandas", "matplotlib", "av",
    "triton", "tensorboard", "numba", "llvmlite", "huggingface_hub", "hf_xet",
)


def _get_root_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        print("ERROR: could not find version in pyproject.toml")
        sys.exit(1)
    return m.group(1)


def sync_versions() -> str:
    """Stamp desktop manifests from pyproject (package.json, lock, tauri, cargo).

    package-lock root version was previously left stale — npm tooling and
    release check scripts then disagree with the sidecar PE version.
    """
    v = _get_root_version()
    changes = []

    pkg_json = ROOT / "desktop" / "package.json"
    if pkg_json.exists():
        pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
        old = pkg.get("version")
        if old != v:
            pkg["version"] = v
            pkg_json.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
            changes.append(f"package.json: {old} -> {v}")

    pkg_lock = ROOT / "desktop" / "package-lock.json"
    if pkg_lock.exists():
        lock = json.loads(pkg_lock.read_text(encoding="utf-8"))
        old_lock = lock.get("version")
        packages = lock.get("packages")
        root_pkg = packages.get("") if isinstance(packages, dict) else None
        old_root = root_pkg.get("version") if isinstance(root_pkg, dict) else None
        if old_lock != v or old_root != v:
            lock["version"] = v
            if isinstance(packages, dict) and isinstance(packages.get(""), dict):
                packages[""]["version"] = v
            pkg_lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
            changes.append(f"package-lock.json: {old_lock} -> {v}")

    tauri_conf = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    if tauri_conf.exists():
        conf = json.loads(tauri_conf.read_text(encoding="utf-8"))
        old = conf.get("version")
        if old != v:
            conf["version"] = v
            tauri_conf.write_text(json.dumps(conf, indent=2) + "\n", encoding="utf-8")
            changes.append(f"tauri.conf.json: {old} -> {v}")

    cargo_toml = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
    if cargo_toml.exists():
        text = cargo_toml.read_text(encoding="utf-8")
        m = re.search(r'(?m)^version\s*=\s*"([^"]*)"', text)
        if m and m.group(1) != v:
            text = re.sub(r'(?m)^(version\s*=\s*)"[^"]*"', rf'\1"{v}"', text, count=1)
            cargo_toml.write_text(text, encoding="utf-8")
            changes.append(f"Cargo.toml: {m.group(1)} -> {v}")

    if changes:
        print(f"Synced version to {v}:")
        for c in changes:
            print(f"  {c}")
    else:
        print(f"Version {v} already synced across all configs.")

    return v


def ensure_pyinstaller():
    """Ensure PyInstaller is available."""
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Installing pyinstaller via uv...")
        subprocess.check_call(
            ["uv", "add", "--dev", "pyinstaller"], cwd=str(ROOT)
        )


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """Parse semver-ish string into a 4-part Windows FILEVERSION tuple."""
    core = version.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def write_sidecar_version_file(version: str) -> Path:
    """Write a PyInstaller --version-file so the sidecar has real PE identity.

    Empty CompanyName/ProductName/FileVersion (0.0.0.0) is a common Windows
    Defender ML signal for Trojan:Win32/Wacatac.!ml and Bearfoos.!ml on
    PyInstaller onefile binaries. Always stamp product metadata at build time.
    """
    out = ROOT / "build" / "pyinstaller" / "remedy-desktop-version.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    maj, min_, pat, build = _version_tuple(version)
    # PyInstaller version-file format (VSVersionInfo). Keep ASCII-safe.
    content = f"""# UTF-8
#
# Remedy Desktop sidecar PE version resource (generated by build_desktop.py)
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({maj}, {min_}, {pat}, {build}),
    prodvers=({maj}, {min_}, {pat}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Remedy'),
        StringStruct(u'FileDescription', u'Remedy Desktop local API sidecar'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'remedy-desktop'),
        StringStruct(u'LegalCopyright', u'Copyright (c) Remedy contributors'),
        StringStruct(u'OriginalFilename', u'remedy-desktop.exe'),
        StringStruct(u'ProductName', u'Remedy Desktop'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    out.write_text(content, encoding="utf-8")
    print(f"Wrote sidecar version resource: {out} (v{version})")
    return out


def get_hidden_imports() -> list[str]:
    """Return the list of hidden imports needed for the remedy server."""
    return [
        # Core dependencies
        "aiohttp",
        "aiohttp.client",
        "aiohttp.client_ws",
        "aiohttp.web",
        "aiohttp.resolver",
        "fastapi",
        "fastapi.middleware",
        # Optional multipart (dev); frozen builds use JSON+base64 uploads
        "multipart",
        "multipart.multipart",
        "multipart.decoders",
        "multipart.exceptions",
        "uvicorn",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "pydantic",
        "pydantic.deprecated",
        "yaml",
        "rich",
        "rich.console",
        "rich.table",
        "rich.panel",
        "rich.prompt",
        "remedy",
        "remedy.interfaces",
        "remedy.interfaces.cli",
        "remedy.interfaces.api",
        "remedy.interfaces.attachments",
        "remedy.interfaces.config",
        "remedy.bundled_skills",
        "remedy.core",
        "remedy.core.agent",
        "remedy.core.runtime",
        "remedy.core.security",
        "remedy.core.providers",
        "remedy.memory",
        "remedy.memory.store",
        "remedy.skills",
        "remedy.skills.tool_registry",
        "remedy.skills.registry",
        "remedy.gateway",
        "remedy.gateway.router",
        "remedy.models",
        "remedy.errors",
        "remedy.core.errors",
        "remedy.persona",
        # Networking / streaming
        "aiosignal",
        "frozenlist",
        "multidict",
        "yarl",
        "charset_normalizer",
        "charset_normalizer.md",
        # ASGI / servers
        "h11",
        "httptools",
        "websockets",
        "websockets.legacy",
        # Standard library modules commonly missed
        "email",
        "email.mime",
        "email.mime.text",
        "json",
        "logging",
        "logging.config",
        "argparse",
        "asyncio",
        "concurrent.futures",
        "multiprocessing",
        "sqlite3",
        "sqlite3.dbapi2",
        "xml",
        "xml.etree",
        "xml.etree.ElementTree",
        "html",
        "http",
    ]


def sidecar_target_triple() -> str:
    """Rust target triple Tauri uses for externalBin (`remedy-desktop-<triple>`)."""
    import platform

    env_triple = os.environ.get("TAURI_ENV_TARGET_TRIPLE", "").strip()
    if env_triple:
        return env_triple
    machine = platform.machine().lower()
    norm = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "i386": "i686",
        "i686": "i686",
        "x86": "i686",
    }
    arch = norm.get(machine, machine)
    sys_name = platform.system().lower()
    if sys_name == "windows":
        return f"{arch}-pc-windows-msvc"
    if sys_name == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def sidecar_bin_paths() -> tuple[Path, Path]:
    """Plain PyInstaller output and the Tauri-triple copy.

    Windows: ``remedy-desktop.exe`` + ``remedy-desktop-x86_64-pc-windows-msvc.exe``.
    Linux: ``remedy-desktop`` + ``remedy-desktop-x86_64-unknown-linux-gnu`` (no .exe).
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    triple = sidecar_target_triple()
    return (
        DESKTOP_BIN / f"remedy-desktop{suffix}",
        DESKTOP_BIN / f"remedy-desktop-{triple}{suffix}",
    )


def build(cache_clean: bool = False, ci: bool = False):
    """Build the standalone remedy-desktop.exe via PyInstaller."""
    print(f"Building Remedy Desktop exe... (root={ROOT})")

    # Always sync package.json / tauri.conf / Cargo.toml from pyproject so CI
    # and local builds never embed mismatched versions.
    v = sync_versions()
    if ci:
        print(f"[CI] Stamped version {v} across manifests before build")
        # Optional: REMEDY_RELEASE_VERSION must match pyproject when set
        # (used by release workflow to catch tag/input skew).
        expected = os.environ.get("REMEDY_RELEASE_VERSION", "").lstrip("v").strip()
        if expected and expected != v:
            print(
                f"ERROR: REMEDY_RELEASE_VERSION={expected} does not match "
                f"pyproject.toml version={v}. Bump with scripts/sync_version.py first."
            )
            sys.exit(1)

    ensure_pyinstaller()

    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)

    if cache_clean:
        pyinstaller_work = ROOT / "build" / "pyinstaller"
        if pyinstaller_work.exists():
            shutil.rmtree(pyinstaller_work)
        print("Cleaned PyInstaller cache.")

    hidden_imports = get_hidden_imports()
    icon_path = ROOT / "desktop" / "src-tauri" / "icons" / "icon.ico"

    src_path = str(ROOT / "src")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "remedy-desktop",
        "--distpath",
        str(DESKTOP_BIN),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build" / "pyinstaller"),
        "--noupx",  # UPX packing raises AV false-positive rates dramatically
        # Prefer repo src/ over any older site-packages remedy-ai install
        "--paths",
        src_path,
        "--add-data",
        f"{ROOT / 'src' / 'remedy'}{os.pathsep}remedy",
        "--add-data",
        f"{ROOT / 'pyproject.toml'}{os.pathsep}.",
    ]
    if sys.platform == "win32":
        # PE identity + no console — Windows-only PyInstaller flags.
        version_file = write_sidecar_version_file(v)
        cmd.extend(["--noconsole", "--version-file", str(version_file)])
        if icon_path.is_file():
            cmd.extend(["--icon", str(icon_path)])
        else:
            print(f"WARNING: sidecar icon missing at {icon_path} (PE will lack icon resource)")

    for hi in hidden_imports:
        cmd.extend(["--hidden-import", hi])

    # Optional extras are runtime downloads, never sidecar payload. A dev
    # machine with remedy-ai[voice]/[voice-hq] installed otherwise ships a
    # 2.6 GB sidecar (torch alone is 3.8 GB unpacked) — CI builds from
    # `uv sync --dev` and never sees them, so guard it here too.
    for mod in SIDECAR_EXCLUDES:
        cmd.extend(["--exclude-module", mod])

    # Entry point
    cmd.extend([
        "--collect-all",
        "remedy",
        "--collect-all",
        "multipart",
        "--hidden-import",
        "remedy.interfaces.xai_auth",
        # CLI is a package after modular split (cli/__init__.py + main)
        str(ROOT / "src" / "remedy" / "interfaces" / "cli" / "__main__.py"),
    ])

    print(f"Running: {' '.join(cmd)}")
    env = os.environ.copy()
    # Force analysis/import from this checkout (editable installs can lag).
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)

    plain, sidecar_path = sidecar_bin_paths()
    if not plain.is_file():
        print(f"\nERROR: Build failed — no sidecar at {plain}")
        sys.exit(1)
    size_mb = plain.stat().st_size / (1024 * 1024)
    print(f"\nBuild complete: {plain} ({size_mb:.1f} MB)")
    shutil.copy2(plain, sidecar_path)
    print(f"Sidecar: {sidecar_path}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build Remedy Desktop standalone exe")
    p.add_argument("--clean", action="store_true", help="Clean PyInstaller cache before build")
    p.add_argument(
        "--stage", action="store_true", help="Copy final installer to dist/ dir"
    )
    p.add_argument(
        "--ci",
        action="store_true",
        help="CI mode — require REMEDY_RELEASE_VERSION match when set; still syncs versions",
    )
    args = p.parse_args()

    code = build(cache_clean=args.clean, ci=args.ci)

    if args.stage:
        candidates = sorted(
            NSIS_DIR.glob("*.exe") if NSIS_DIR.exists() else [],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            DIST_DIR.mkdir(exist_ok=True)
            dest = DIST_DIR / candidates[0].name
            shutil.copy2(candidates[0], dest)
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f"\nStaged installer: {dest} ({size_mb:.1f} MB)")
        else:
            print("\nNo NSIS installer found — run tauri build first.")

    raise SystemExit(code)

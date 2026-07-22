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


def _get_root_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        print("ERROR: could not find version in pyproject.toml")
        sys.exit(1)
    return m.group(1)


def sync_versions() -> str:
    v = _get_root_version()
    changes = []

    pkg_json = ROOT / "desktop" / "package.json"
    if pkg_json.exists():
        pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
        if pkg.get("version") != v:
            pkg["version"] = v
            pkg_json.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
            changes.append(f"package.json: {pkg.get('version')} -> {v}")

    tauri_conf = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    if tauri_conf.exists():
        conf = json.loads(tauri_conf.read_text(encoding="utf-8"))
        if conf.get("version") != v:
            conf["version"] = v
            tauri_conf.write_text(json.dumps(conf, indent=2) + "\n", encoding="utf-8")
            changes.append(f"tauri.conf.json: {conf.get('version')} -> {v}")

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
        print("Installing pyinstaller...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )


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
        "remedy.interfaces.config",
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


def build(cache_clean: bool = False):
    """Build the standalone remedy-desktop.exe via PyInstaller."""
    print(f"Building Remedy Desktop exe... (root={ROOT})")

    sync_versions()
    ensure_pyinstaller()

    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)

    if cache_clean:
        pyinstaller_work = ROOT / "build" / "pyinstaller"
        if pyinstaller_work.exists():
            shutil.rmtree(pyinstaller_work)
        print("Cleaned PyInstaller cache.")

    hidden_imports = get_hidden_imports()

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
        "--noupx",
        "--noconsole",
        "--add-data",
        f"{ROOT / 'src' / 'remedy'}{os.pathsep}remedy",
    ]

    for hi in hidden_imports:
        cmd.extend(["--hidden-import", hi])

    # Entry point
    cmd.extend([
        "--collect-all",
        "remedy",
        str(ROOT / "src" / "remedy" / "interfaces" / "cli.py"),
    ])

    print(f"Running: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(ROOT))

    exe_path = DESKTOP_BIN / "remedy-desktop.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild complete: {exe_path} ({size_mb:.1f} MB)")
    else:
        print("\nERROR: Build failed - no .exe produced")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build Remedy Desktop standalone exe")
    p.add_argument("--clean", action="store_true", help="Clean PyInstaller cache before build")
    p.add_argument(
        "--stage", action="store_true", help="Copy final installer to dist/ dir"
    )
    args = p.parse_args()

    code = build(cache_clean=args.clean)

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

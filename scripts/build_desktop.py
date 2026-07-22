"""PyInstaller build script for Remedy Desktop.

Creates a standalone Windows .exe for the remedy CLI server,
suitable for bundling as a Tauri sidecar.

Usage:
    python scripts/build_desktop.py          # build standalone exe
    python scripts/build_desktop.py --clean  # clean build from scratch
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_BIN = ROOT / "desktop" / "bin"


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
        "--console",  # show console window for the server
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
    args = p.parse_args()

    build(cache_clean=args.clean)

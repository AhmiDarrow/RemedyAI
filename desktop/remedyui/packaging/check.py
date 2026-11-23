#!/usr/bin/env python3
"""Static checks for the 1.0 packaging contract. No compiler, no network.

Run from anywhere: python packaging/check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent.parent
TAURI_CONF = REPO / "desktop" / "src-tauri" / "tauri.conf.json"
SIGNING = REPO / "docs" / "WINDOWS_SIGNING.md"
WORKFLOW = REPO / ".github" / "workflows" / "remedyui-desktop-package.yml"

PUBKEY = "RWTgvKzjXQwB1gQ5itS9iH5TjbPetoDAi4OLolIxjPrNnmRyd3qJJ4u6"
PUBKEY_B64 = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQ2MDEwQzVERTNBQ0JDRTAK"
    "UldUZ3ZLempYUXdCMWdRNWl0UzlpSDVUamJQZXRvREFpNE9Mb2xJeGpQck5ubVJ5ZDNxSko0dTYK"
)

failures: list[str] = []


def need(cond: bool, message: str) -> None:
    if not cond:
        failures.append(message)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    cargo = text(ROOT / "Cargo.toml")
    need('version = "1.0.0"' in cargo, "Cargo.toml version is not 1.0.0")

    feed = json.loads(text(HERE / "latest.json"))
    need(feed.get("version") == "v1.0.0", "latest.json version")
    platforms = feed.get("platforms") or {}
    expected = {
        "windows-x86_64": "/v1.0.0/Remedy.Desktop_1.0.0_x64-setup.exe",
        "linux-x86_64": "/v1.0.0/Remedy.Desktop_1.0.0_amd64.AppImage",
        "darwin-aarch64": "/runtime-v1.0.0/Remedy.Desktop_1.0.0_aarch64.app.tar.gz",
        "darwin-x86_64": "/runtime-v1.0.0/Remedy.Desktop_1.0.0_x64.app.tar.gz",
    }
    for key, suffix in expected.items():
        entry = platforms.get(key) or {}
        url = entry.get("url") or ""
        need(url.endswith(suffix), f"{key} url {url}")
        need("signature" in entry, f"{key} missing signature")
        need(entry.get("signature") == "", f"{key} template signature must stay empty")
        need("Remedy_Desktop_" not in url, f"{key} uses a spaced-name typo")

    nsi = text(HERE / "windows" / "installer.nsi")
    for snippet in (
        "com.remedy.desktop",
        "Remedy.Desktop_${VERSION}_x64-setup.exe",
        "/UPDATE",
        "Function UninstallPrevious",
        "ExecWait '\"$R1\" /S /UPDATE _?=$R2'",
        "hooks.nsh",
        "!ifndef HOOKS",
        r"${__FILEDIR__}\..\..\..\src-tauri\windows\hooks.nsh",
        r"$LOCALAPPDATA\${PRODUCTNAME}",
        r"Uninstall\${PRODUCTNAME}",
        "!ifndef RUNTIMESRCPATH",
        "!ifndef CORESRCPATH",
        r'File "/oname=remedy-runtime.exe" "${RUNTIMESRCPATH}"',
        r'File "/oname=remedy_core.dll" "${CORESRCPATH}"',
        r'Delete "$INSTDIR\remedy-runtime.exe"',
        r'Delete "$INSTDIR\remedy_core.dll"',
    ):
        need(snippet in nsi, f"installer.nsi missing {snippet}")
    need("/.remedy" not in nsi and "\\.remedy" not in nsi,
         "installer.nsi must not name the per-user data directory")

    hooks = text(REPO / "desktop" / "src-tauri" / "windows" / "hooks.nsh")
    for snippet in (
        "/NOAUTOLAUNCH",
        "IfSilent",
        "Update-mode uninstall: keeping user data",
        "Silent uninstall: keeping user data",
        "RemedyDesktop-UpdaterOwnsRelaunch.flag",
    ):
        need(snippet in hooks, f"hooks.nsh missing {snippet}")

    updater = text(ROOT / "src" / "update.rs")
    for snippet in (
        "/S",
        "/NCRC",
        "/UPDATE",
        "/NOAUTOLAUNCH",
        '$nsisArgs = @(\'/S\', \'/NCRC\', \'/UPDATE\', \'/NOAUTOLAUNCH\')',
        '$nsisArgs += "/D=$priorDir"',
        "RemedyDesktop-UpdaterOwnsRelaunch.flag",
        "Join-Path $priorDir 'Remedy Desktop.exe'",
        "appimage_stage_path",
        "archive_entry_is_safe",
        PUBKEY,
        "Feed::Json",
    ):
        need(snippet in updater, f"update.rs missing {snippet}")
    waiter_region = updater.split("pub fn windows_waiter_script", 1)[1].split("pub fn check_feed", 1)[0]
    raw_start = waiter_region.index('r#"') + 3
    raw_end = waiter_region.index('"#', raw_start)
    waiter = (
        waiter_region[raw_start:raw_end]
        .replace("{log}", "LOG")
        .replace("{flag}", "FLAG")
        .replace("{installer}", "INSTALLER")
        .replace("{prior}", "PRIOR")
        .replace("{pid}", "PID")
        .replace("{{", "{")
        .replace("}}", "}")
    )
    need("/.remedy" not in waiter and "\\.remedy" not in waiter,
         "waiter script must not touch the per-user data directory")
    need(waiter.count("{") == waiter.count("}"), "waiter script braces do not balance")
    need(waiter.count("(") == waiter.count(")"), "waiter script parentheses do not balance")
    need("$nsisArgs += \"/D=$priorDir\"" in waiter, "generated waiter lost /D=")

    conf = text(TAURI_CONF)
    signing = text(SIGNING)
    need(PUBKEY_B64 in conf, "tauri.conf.json pubkey changed")
    need(PUBKEY_B64 in signing, "WINDOWS_SIGNING.md pubkey changed")
    need("com.remedy.desktop" in conf, "tauri identifier changed")

    win = text(HERE / "package-windows.ps1")
    linux = text(HERE / "package-linux.sh")
    mac = text(HERE / "package-macos.sh")
    need("--jobs $Jobs" in win or "--jobs ${JOBS}" in win or "--jobs $Jobs" in win, "windows jobs")
    need('--jobs "${JOBS}"' in linux and 'JOBS="${JOBS:-4}"' in linux, "linux jobs default")
    need('--jobs "${JOBS}"' in mac and 'JOBS="${JOBS:-4}"' in mac, "macos jobs default")
    need('"/DHOOKS=' in win, "windows script must pass an absolute hooks path")
    need('"/DRUNTIMESRCPATH=' in win, "windows script must pass the remedy-runtime path")
    need('"/DCORESRCPATH=' in win, "windows script must pass the remedy_core.dll path")
    need("go build" in win and "remedy-runtime" in win, "windows script must build remedy-runtime")
    need("zig build" in win and "remedy_core.dll" in win, "windows script must build remedy_core.dll")
    need("go build" in linux and "remedy-runtime" in linux, "linux script must build and pack remedy-runtime")
    need("libremedy_core.so" in linux and "zig build" in linux, "linux script must pack libremedy_core.so")
    need("go build" in mac and "Contents/MacOS/remedy-runtime" in mac, "macos script must pack remedy-runtime next to the app")
    need("libremedy_core.dylib" in mac and "zig build" in mac, "macos script must pack libremedy_core.dylib")
    need("-srcfolder \"${STAGE}\"" in mac, "dmg must be built from the stage directory")
    need("Remedy Desktop.app" in mac, "macos tarball name")
    need("3e00" in linux, "linux script must require an x86_64 ELF")

    workflow = text(WORKFLOW)
    need("workflow_dispatch" in workflow, "package workflow must be manual")
    need("action-gh-release" not in workflow, "workflow must not publish a release")
    need("gh release" not in workflow, "workflow must not call gh release")
    need("softprops" not in workflow, "workflow must not use the release action")
    for bad in ("tags:", "push:"):
        need(bad not in workflow, f"workflow must not run on {bad}")
    need("macos-15-intel" in workflow, "intel macOS runner")
    need("minisign-0.12" in workflow, "pin the existing minisign release")
    need("CARGO_BUILD_JOBS" in workflow, "cap cargo jobs in CI")

    if failures:
        print("packaging check failed:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("packaging check ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

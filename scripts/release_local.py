"""Publish a Remedy release from this machine.

There is no CI on GitHub. The full matrix runs here (``scripts/prepush.py``),
and GitHub only builds and signs the release packages (``desktop-release.yml``,
on a ``v*`` tag push), because the updater signing key lives there as a secret
and Linux / macOS packages need their own runners. This script does the rest,
exactly as the old GitHub release job did:

    uv run python scripts/release_local.py v0.72.0 --dry-run   # check, stage, no publishing
    uv run python scripts/release_local.py v0.72.0             # publish both releases + PyPI
    uv run python scripts/release_local.py v0.72.0 --no-pypi   # releases only

1. The tag must point at HEAD, every version surface must read it, the commit
   must be on ``origin/master``, and this machine's gate must have recorded a
   green full-matrix run for it.
2. Wait for GitHub's build run for the tag, then download its artifacts.
3. Stage the standalone runtime assets and ``remedy-native-SHA256SUMS.txt``,
   write ``latest.json``, split the files between the main release (what a
   person downloads, plus ``latest.json``) and the ``runtime-v<ver>``
   companion prerelease (pip first-run assets, macOS updater tarballs, every
   ``.sig``), and check both sets.
4. Publish the companion release first, then the main release. Publishing the
   companion release triggers ``verify-release-assets.yml``, which re-checks
   the macOS and ARM binaries on native runners.
5. Build the PyPI wheel with the WebUI and the runtime hashes pinned from
   step 3, re-verify the published assets against the pins, check the wheel,
   and ``uv publish`` it (credentials from the environment or ``~/.pypirc``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "AhmiDarrow"
REPO = "RemedyAI"
WORKFLOW = "desktop-release.yml"
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")

# Every native asset the companion release must carry beyond the x86_64 pair.
REQUIRED_NATIVE = (
    "remedy-runtime-aarch64-unknown-linux-gnu",
    "libremedy_core-aarch64-unknown-linux-gnu.so",
    "remedy-runtime-aarch64-apple-darwin",
    "libremedy_core-aarch64-apple-darwin.dylib",
    "remedy-runtime-x86_64-apple-darwin",
    "libremedy_core-x86_64-apple-darwin.dylib",
    "remedy-runtime-aarch64-pc-windows-msvc.exe",
    "remedy_core-aarch64-pc-windows-msvc.dll",
)
WANT_MAIN = (
    r"_x64-setup\.exe$",
    r"^latest\.json$",
    r"_amd64\.AppImage$",
    r"_amd64\.deb$",
    r"_aarch64\.dmg$",
    r"_x64\.dmg$",
)
# Artifact name in the GitHub run -> staging directory (the old job's layout).
ARTIFACT_DIRS = {
    "remedy-installer": "dist",
    "remedy-installer-sig": "dist",
    "remedy-linux": "dist-linux",
    "remedy-macos-aarch64": "dist-macos",
    "remedy-macos-x64": "dist-macos",
    "remedy-runtime-bins": "dist-native-win",
    "remedy-runtime-linux": "dist-native-linux",
    "remedy-runtime-linux-arm": "dist-native-linux-arm",
    "remedy-runtime-windows-arm64": "dist-native-win-arm",
}
MACOS_RUNTIME_PREFIX = "remedy-runtime-macos-"


class ReleaseError(RuntimeError):
    pass


def _say(msg: str) -> None:
    print(msg, flush=True)


def _run(cmd: list[str], *, capture: bool = False, cwd: Path = ROOT) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True, encoding="utf-8")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise ReleaseError(f"{' '.join(cmd)} failed ({proc.returncode}) {detail}".strip())
    return (proc.stdout or "").strip() if capture else ""


# --------------------------------------------------------------------------- #
# Pure helpers (tested in tests/test_ci_matrix.py)
# --------------------------------------------------------------------------- #


def unwrap_sig(raw: str) -> str:
    """Raw minisign text from a Tauri ``.sig`` (which may be base64-wrapped).

    The updater wants the raw form (``untrusted comment: ...``); see AGENTS.md
    "Desktop installer / auto-update naming"."""
    sig = (raw or "").strip()
    if not sig:
        return ""
    try:
        decoded = base64.b64decode(sig, validate=True).decode("utf-8")
    except Exception:
        decoded = ""
    if decoded.lstrip().startswith("untrusted comment:"):
        sig = decoded if decoded.endswith("\n") else decoded + "\n"
    return sig


def is_prerelease(changelog: str, version: str) -> bool:
    """A version whose CHANGELOG section says "experimental" ships as a
    prerelease, so installed apps never auto-update to it."""
    on = False
    section: list[str] = []
    for line in changelog.splitlines():
        if line.startswith(f"## [{version}]"):
            on = True
            continue
        if on and line.startswith("## ["):
            break
        if on:
            section.append(line)
    return "experimental" in "\n".join(section).lower()


def safe_asset_name(name: str) -> str:
    """GitHub turns spaces in asset names into dots; name files that way first
    so latest.json and the uploaded asset always agree."""
    return name.replace(" ", ".")


def build_latest_json(
    *,
    version: str,
    installer_asset: str,
    windows_sig: str,
    appimage: str,
    linux_sig: str,
    darwin: dict[str, dict[str, str]],
    pub_date: str,
) -> dict[str, object]:
    tag = f"v{version}"
    runtime_tag = f"runtime-v{version}"
    base = f"https://github.com/{OWNER}/{REPO}/releases/download"
    if not windows_sig:
        raise ReleaseError("empty Windows updater signature")
    platforms: dict[str, dict[str, str]] = {
        "windows-x86_64": {"signature": windows_sig, "url": f"{base}/{tag}/{installer_asset}"},
    }
    if appimage:
        platforms["linux-x86_64"] = {"signature": linux_sig, "url": f"{base}/{tag}/{appimage}"}
    for key in ("darwin-aarch64", "darwin-x86_64"):
        info = darwin.get(key) or {}
        if not info.get("url_name") or not info.get("signature"):
            raise ReleaseError(f"missing signed macOS updater asset for {key}")
        # macOS updater tarballs live on the runtime-v companion release.
        platforms[key] = {"signature": info["signature"], "url": f"{base}/{runtime_tag}/{info['url_name']}"}
    return {
        "version": f"v{version}",
        "notes": f"Remedy Desktop v{version} — Windows installer, Linux AppImage, and macOS app",
        "pub_date": pub_date,
        "platforms": platforms,
    }


def darwin_entries(names_and_sigs: dict[str, str]) -> dict[str, dict[str, str]]:
    """{filename: sig text or ""} from dist-macos -> darwin platform entries."""
    darwin: dict[str, dict[str, str]] = {}
    for name, body in sorted(names_and_sigs.items()):
        if name.endswith(".app.tar.gz"):
            if "_aarch64.app.tar.gz" in name:
                darwin.setdefault("darwin-aarch64", {})["url_name"] = name
            elif "_x64.app.tar.gz" in name or "_x86_64.app.tar.gz" in name:
                darwin.setdefault("darwin-x86_64", {})["url_name"] = name
        if name.endswith(".app.tar.gz.sig"):
            sig = unwrap_sig(body)
            if "_aarch64.app.tar.gz.sig" in name:
                darwin.setdefault("darwin-aarch64", {})["signature"] = sig
            elif "_x64.app.tar.gz.sig" in name or "_x86_64.app.tar.gz.sig" in name:
                darwin.setdefault("darwin-x86_64", {})["signature"] = sig
    return darwin


def check_release_sets(
    version: str, main: list[str], runtime: list[str], sums_text: str, manifest: dict[str, object]
) -> list[str]:
    """Every consistency rule the old release job enforced. Empty = OK."""
    errors: list[str] = []
    if len(main) != len(WANT_MAIN):
        errors.append(f"main release must have {len(WANT_MAIN)} files, has {len(main)}")
    for pat in WANT_MAIN:
        if sum(1 for n in main if re.search(pat, n)) != 1:
            errors.append(f"main release needs exactly one file matching {pat}")
    for name in (
        "remedy-native-SHA256SUMS.txt",
        "remedy-voice-female.wav",
        "remedy-voice-male.wav",
        f"Remedy.Desktop_{version}_aarch64.app.tar.gz",
        f"Remedy.Desktop_{version}_x64.app.tar.gz",
        f"Remedy.Desktop_{version}_aarch64.app.tar.gz.sig",
        f"Remedy.Desktop_{version}_x64.app.tar.gz.sig",
    ):
        if name not in runtime:
            errors.append(f"runtime release is missing {name}")
    for line in sums_text.splitlines():
        if not line.strip():
            continue
        name = line.split()[-1].lstrip("*")
        if name not in runtime:
            errors.append(f"SHA256SUMS lists {name} but it is not staged")
    if any(n.endswith(".sig") for n in main):
        errors.append("signatures belong on the runtime release")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict):
        return errors + ["latest.json has no platforms"]
    for key, info in platforms.items():
        url = info["url"]
        name = url.rsplit("/", 1)[-1]
        home = runtime if f"/download/runtime-v{version}/" in url else main
        if not any(safe_asset_name(n) == name for n in home):
            errors.append(f"latest.json {key} points at {url}, which is not staged")
        if not info.get("signature"):
            errors.append(f"latest.json {key} has no signature")
    return errors


def sha256sums(paths: list[Path]) -> str:
    rows = []
    for p in sorted(paths, key=lambda q: q.name):
        rows.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    return "\n".join(sorted(rows)) + "\n"


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def check_ready(tag: str) -> str:
    """The prepush release gate, run here: tag at HEAD, versions aligned, on
    origin/master, and this machine's green record for the commit."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import prepush  # noqa: E402  (scripts/ is not a package)

    try:
        prepush.check_release(tag)
    except SystemExit as exc:
        raise ReleaseError(f"release gate refused {tag}") from exc
    return _run(["git", "rev-parse", f"{tag}^{{commit}}"], capture=True)


def wait_for_build(tag: str, *, timeout_min: int) -> str:
    """The run id of GitHub's build for `tag`, once it has completed green."""
    deadline = time.time() + timeout_min * 60
    while True:
        out = _run(
            [
                "gh", "run", "list", "-R", f"{OWNER}/{REPO}", "--workflow", WORKFLOW,
                "--branch", tag, "--event", "push", "--limit", "5",
                "--json", "databaseId,status,conclusion,headBranch,url",
            ],
            capture=True,
        )
        runs = [r for r in json.loads(out or "[]") if r.get("headBranch") == tag]
        if runs:
            run = runs[0]
            if run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise ReleaseError(f"GitHub build for {tag} concluded {run['conclusion']}: {run['url']}")
                _say(f"  [ok] GitHub build green: {run['url']}")
                return str(run["databaseId"])
            _say(f"  waiting for GitHub build ({run['status']}): {run['url']}")
        else:
            _say(f"  no GitHub build for {tag} yet (push the tag to start one)")
        if time.time() > deadline:
            raise ReleaseError(f"GitHub build for {tag} did not finish within {timeout_min} min")
        time.sleep(60)


def download(run_id: str, work: Path) -> None:
    dl = work / "artifacts"
    if dl.exists():
        shutil.rmtree(dl)
    _run(["gh", "run", "download", run_id, "-R", f"{OWNER}/{REPO}", "-D", str(dl)])
    found = {p.name for p in dl.iterdir() if p.is_dir()}
    for name, sub in ARTIFACT_DIRS.items():
        src = dl / name
        if not src.is_dir():
            if name == "remedy-installer-sig":
                continue  # optional on its own; the signature check below is strict
            raise ReleaseError(f"GitHub build has no artifact {name} (found: {', '.join(sorted(found))})")
        dest = work / sub
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file():
                target = dest / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
    mac = [p for p in dl.iterdir() if p.is_dir() and p.name.startswith(MACOS_RUNTIME_PREFIX)]
    if not mac:
        raise ReleaseError("GitHub build has no macOS runtime artifacts")
    dest = work / "dist-native-macos"
    dest.mkdir(parents=True, exist_ok=True)
    for d in mac:
        for f in d.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)


def stage_native(work: Path) -> Path:
    out = work / "dist-native"
    out.mkdir(parents=True, exist_ok=True)
    win, lin = work / "dist-native-win", work / "dist-native-linux"
    pairs = [
        (win / "remedy-runtime-x86_64-pc-windows-msvc.exe", "remedy-runtime-x86_64-pc-windows-msvc.exe"),
        (win / "remedy_core.dll", "remedy_core-x86_64-pc-windows-msvc.dll"),
        (lin / "remedy-runtime-x86_64-unknown-linux-gnu", "remedy-runtime-x86_64-unknown-linux-gnu"),
        (lin / "libremedy_core.so", "libremedy_core-x86_64-unknown-linux-gnu.so"),
    ]
    if not pairs[0][0].is_file() and (win / "remedy-runtime.exe").is_file():
        pairs[0] = (win / "remedy-runtime.exe", pairs[0][1])
    if not pairs[2][0].is_file() and (lin / "remedy-runtime").is_file():
        pairs[2] = (lin / "remedy-runtime", pairs[2][1])
    for src, name in pairs:
        if src.is_file():
            shutil.copy2(src, out / name)
    for sub in ("dist-native-linux-arm", "dist-native-macos", "dist-native-win-arm"):
        d = work / sub
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and (f.name.startswith("remedy-runtime-") or "remedy_core" in f.name):
                    shutil.copy2(f, out / f.name)
    missing = [n for n in REQUIRED_NATIVE if not (out / n).is_file()]
    if missing:
        raise ReleaseError("missing required native assets: " + ", ".join(missing))
    refs = ROOT / "assets" / "voice-references"
    expected = {}
    for line in (refs / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split()[0], line.split()[-1].lstrip("*")
            expected[name] = digest
    for wav, dest_name in (("female.wav", "remedy-voice-female.wav"), ("male.wav", "remedy-voice-male.wav")):
        src = refs / wav
        if not src.is_file():
            raise ReleaseError(f"missing HQ voice reference {src}")
        if hashlib.sha256(src.read_bytes()).hexdigest() != expected.get(wav):
            raise ReleaseError(f"{src} does not match assets/voice-references/SHA256SUMS.txt")
        shutil.copy2(src, out / dest_name)
    files = [
        f for f in out.iterdir()
        if f.is_file() and (f.name.startswith("remedy-runtime-") or "remedy_core" in f.name or f.name.startswith("remedy-voice-"))
    ]
    (out / "remedy-native-SHA256SUMS.txt").write_text(sha256sums(files), encoding="utf-8")
    return out


def assemble(work: Path, version: str) -> tuple[Path, Path]:
    dist = work / "dist"
    installers = list(dist.glob("*.exe"))
    if len(installers) != 1:
        raise ReleaseError(f"expected one Windows installer, found {len(installers)}")
    installer = installers[0]
    safe = dist / safe_asset_name(installer.name)
    if safe != installer:
        installer.rename(safe)
        sig_old = dist / (installer.name + ".sig")
        if sig_old.is_file():
            sig_old.rename(dist / (safe.name + ".sig"))
        installer = safe
    sigs = list(dist.glob("*.sig"))
    if not sigs:
        raise ReleaseError("no Windows updater signature: the GitHub build must sign (TAURI_SIGNING_PRIVATE_KEY secret)")
    windows_sig = unwrap_sig(sigs[0].read_text(encoding="utf-8")[:100000].replace("\r", ""))
    linux = work / "dist-linux"
    appimage, linux_sig = "", ""
    for p in sorted(linux.iterdir()):
        if p.name.endswith(".AppImage"):
            appimage = p.name
        if p.name.endswith(".AppImage.sig"):
            linux_sig = unwrap_sig(p.read_text(encoding="utf-8"))
    mac_dir = work / "dist-macos"
    darwin = darwin_entries({
        p.name: (p.read_text(encoding="utf-8") if p.name.endswith(".sig") else "")
        for p in mac_dir.iterdir() if p.is_file()
    })
    manifest = build_latest_json(
        version=version,
        installer_asset=installer.name,
        windows_sig=windows_sig,
        appimage=appimage,
        linux_sig=linux_sig,
        darwin=darwin,
        pub_date=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    (dist / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    main, runtime = work / "release-main", work / "release-runtime"
    for d in (main, runtime):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
    shutil.copy2(installer, main)
    shutil.copy2(dist / "latest.json", main)
    for p in linux.iterdir():
        if p.name.endswith((".AppImage", ".deb")):
            shutil.copy2(p, main)
    for p in mac_dir.glob("*.dmg"):
        shutil.copy2(p, main)
    for p in (work / "dist-native").iterdir():
        shutil.copy2(p, runtime)
    for p in mac_dir.glob("*.app.tar.gz"):
        shutil.copy2(p, runtime)
    for d in (dist, linux, mac_dir):
        for p in d.glob("*.sig"):
            shutil.copy2(p, runtime)
    errors = check_release_sets(
        version,
        sorted(p.name for p in main.iterdir()),
        sorted(p.name for p in runtime.iterdir()),
        (runtime / "remedy-native-SHA256SUMS.txt").read_text(encoding="utf-8"),
        manifest,
    )
    if errors:
        raise ReleaseError("release files are inconsistent:\n  " + "\n  ".join(errors))
    _say(f"  [ok] {len(list(main.iterdir()))} main files, {len(list(runtime.iterdir()))} runtime files")
    return main, runtime


def runtime_notes(version: str) -> str:
    return (
        f"Support files for [Remedy Desktop v{version}](https://github.com/{OWNER}/{REPO}/releases/tag/v{version}). "
        "**You do not need to download anything here.**\n\n"
        "- `pip install remedy-ai` fetches `remedy-runtime` / `remedy_core` and the HQ voice references from this "
        "release on first run and checks each file against the sha256 pinned inside the wheel (the PyPI wheel is "
        "built from this file after the release).\n"
        "- The in-app updater downloads the macOS `.app.tar.gz` from here. Every `.sig` is a minisign signature "
        "for an updater file.\n\n"
        f"Installers are on the [v{version} release](https://github.com/{OWNER}/{REPO}/releases/tag/v{version}).\n"
    )


def main_notes(version: str, prerelease: bool) -> str:
    pre = (
        "> **Experimental pre-release.** This line is still being proven in daily use. Installed apps do not "
        "auto-update to it; the stable release stays the one marked Latest.\n\n"
        if prerelease else ""
    )
    v = version
    return f"""## Remedy Desktop v{v}

{pre}### Which file do I need?

| Your computer | Download |
| --- | --- |
| Windows (x64) | `Remedy.Desktop_{v}_x64-setup.exe` |
| Mac with Apple Silicon (M1 and later) | `Remedy.Desktop_{v}_aarch64.dmg` |
| Mac with Intel | `Remedy.Desktop_{v}_x64.dmg` |
| Linux (x86_64) | `.AppImage` (any distro) or `.deb` (Debian/Ubuntu) |
| Command line, any OS | `pip install remedy-ai` |

The Mac app is unsigned: the first time, right-click it in Applications and choose **Open**. `latest.json` is for the in-app updater. Runtime and signature files are on the [runtime-v{v}](https://github.com/{OWNER}/{REPO}/releases/tag/runtime-v{v}) companion release, and you never need to download them.

One partner on **Windows, Linux, and macOS**. Continuity stays in `~/.remedy`.

What changed: [What's new](https://github.com/{OWNER}/{REPO}/blob/master/docs/manual/13-whats-new.md).

### Windows
1. Download **`Remedy.Desktop_*_x64-setup.exe`**
2. Run the installer
3. Launch from the Start Menu or desktop shortcut

### Linux
- **`.deb`** — `sudo apt install ./Remedy.Desktop_*_amd64.deb` (Debian/Ubuntu)
- **AppImage** — `chmod +x Remedy.Desktop_*.AppImage && ./Remedy.Desktop_*.AppImage`

Close (✕) on Linux / WSLg minimizes to the taskbar (no tray). Maximize uses the work area of the monitor the window is on.

### macOS
1. Download **`Remedy.Desktop_*_aarch64.dmg`** (Apple Silicon) or **`Remedy.Desktop_*_x64.dmg`** (Intel)
2. Drag Remedy Desktop to Applications
3. This build is unsigned (ad-hoc codesign, no Apple Developer credentials or notarization). First launch: right-click the app and choose Open, or run `xattr -dr com.apple.quarantine "/Applications/Remedy Desktop.app"`
4. In-app updates use the minisign-signed `.app.tar.gz`, same as Windows and Linux

No Python, Node, or Rust required.

### CLI runtime (`pip install remedy-ai`)

The first `remedy serve` on a PyPI install downloads the standalone `remedy-runtime` / `remedy_core` pair for this OS from the `runtime-v{v}` companion release, verifies it against the checksums pinned inside the wheel, and caches it under `~/.remedy/runtime/`. Chatterbox HQ downloads `remedy-voice-*.wav` the same way into `~/.remedy/voice/references/`.

### Auto-update
This release is **minisign-signed**. Windows NSIS, Linux AppImage, and macOS `.app.tar.gz` installs with the embedded updater pubkey (v0.10.2+) can check and install updates in-app. Linux `.deb` installs still update from this page (`sudo apt install` the new `.deb`). macOS builds are not Apple-notarized.
"""


def publish_releases(version: str, sha: str, main: Path, runtime: Path, prerelease: bool, work: Path) -> None:
    repo = f"{OWNER}/{REPO}"
    rnotes = work / "runtime-notes.md"
    rnotes.write_text(runtime_notes(version), encoding="utf-8")
    _run([
        "gh", "release", "create", f"runtime-v{version}", "-R", repo, "--target", sha,
        "--title", f"Remedy runtime assets v{version}", "--prerelease", "--latest=false",
        "--notes-file", str(rnotes), *[str(p) for p in sorted(runtime.iterdir())],
    ])
    _say(f"  [ok] published runtime-v{version}")
    mnotes = work / "main-notes.md"
    mnotes.write_text(main_notes(version, prerelease), encoding="utf-8")
    cmd = [
        "gh", "release", "create", f"v{version}", "-R", repo, "--verify-tag",
        "--title", f"Remedy Desktop v{version}", "--notes-file", str(mnotes), "--generate-notes",
    ]
    if prerelease:
        cmd += ["--prerelease", "--latest=false"]
    cmd += [str(p) for p in sorted(main.iterdir())]
    _run(cmd)
    _say(f"  [ok] published v{version}")


def build_wheel(work: Path, *, published: bool, upload: bool) -> None:
    sums = work / "release-runtime" / "remedy-native-SHA256SUMS.txt"
    _run(["uv", "run", "python", "scripts/stage_wheel_webui.py", "--build"])
    _run(["uv", "run", "python", "scripts/pin_native_hashes.py", "--sums", str(sums)])
    pins = ROOT / "src" / "remedy" / "runtime" / "native_pins.json"
    if published:
        built = pins.read_bytes()
        _run(["uv", "run", "python", "scripts/pin_native_hashes.py", "--from-release", "--verify-assets"])
        if pins.read_bytes() != built:
            raise ReleaseError("the published checksum file differs from the one this run built")
    _run(["uv", "run", "python", "scripts/pin_native_hashes.py", "--check"])
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    _run(["uv", "build"])
    wheels = sorted(str(p) for p in dist.glob("*.whl"))
    _run(["uv", "run", "python", "scripts/check_wheel.py", "--release", *wheels])
    _say("  [ok] wheel built and checked: " + ", ".join(Path(w).name for w in wheels))
    if upload:
        _run(["uv", "publish"])
        _say("  [ok] published to PyPI")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", help="release tag, e.g. v0.72.0")
    parser.add_argument("--dry-run", action="store_true", help="check and stage everything; publish nothing")
    parser.add_argument("--no-pypi", action="store_true", help="publish the GitHub releases only")
    parser.add_argument("--wait-min", type=int, default=180, help="how long to wait for GitHub's build")
    parser.add_argument("--work", default=str(ROOT / ".release-work"), help="staging directory (gitignored)")
    args = parser.parse_args(argv)

    tag = args.tag if args.tag.startswith("v") else f"v{args.tag}"
    if not RELEASE_TAG_RE.match(tag):
        _say(f"release_local: '{tag}' is not a release tag")
        return 2
    version = tag[1:]
    work = Path(args.work)
    try:
        _say(f"release_local: {tag}{' (dry run)' if args.dry_run else ''}")
        sha = check_ready(tag)
        run_id = wait_for_build(tag, timeout_min=args.wait_min)
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        download(run_id, work)
        stage_native(work)
        main_dir, runtime_dir = assemble(work, version)
        prerelease = is_prerelease((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
        _say(f"  channel: {'prerelease' if prerelease else 'stable'}")
        if not args.dry_run:
            publish_releases(version, sha, main_dir, runtime_dir, prerelease, work)
        if not args.no_pypi:
            build_wheel(work, published=not args.dry_run, upload=not args.dry_run)
    except ReleaseError as exc:
        _say(f"release_local: FAILED: {exc}")
        return 1
    _say("release_local: done." if not args.dry_run else "release_local: dry run passed; nothing was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

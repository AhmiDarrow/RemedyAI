"""Stage Go remedy-runtime + Zig remedy_core + RMDY pyz for Desktop packaging.

Packaged Desktop and local ``tauri build`` / ``tauri:dev`` need:

* ``desktop/bin/remedy-runtime`` (+ Tauri target-triple copy) — ``externalBin``
* ``desktop/bin/remedy_core.dll`` / ``libremedy_core.so`` / ``libremedy_core.dylib``
  — Tauri resource
* ``desktop/bin/rmdy_tool_worker.pyz`` — Tauri resource (Python zipapp worker)

Release CI (``desktop-release.yml``) builds the same artifacts inline. This
script is the local equivalent so developers do not hand-copy binaries.
Legacy ``remedy-desktop`` onefile sidecars are not built or staged.

Usage:
    python scripts/build_desktop.py                      # build + stage
    python scripts/build_desktop.py --clean              # wipe desktop/bin first
    python scripts/build_desktop.py --core-lib PATH      # stage this core library
    python scripts/build_desktop.py --ci                 # release mode (version gate)
    python scripts/build_desktop.py --skip-runtime       # only stage Zig core
    python scripts/build_desktop.py --skip-core          # only build Go runtime
"""

from __future__ import annotations

import json
import os
import platform
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
GO_MOD = ROOT / "native" / "go"
ZIG_DIR = ROOT / "native" / "zig"

# Keep in lockstep with remedy.runtime.native_runtime._ABI_VERSION and
# REMEDY_CORE_ABI_VERSION in native/zig/include/remedy_core.h.
REQUIRED_CORE_ABI = 5


def _get_root_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        print("ERROR: could not find version in pyproject.toml")
        sys.exit(1)
    return m.group(1)


def sync_versions() -> str:
    """Stamp desktop manifests from pyproject (package.json, lock, tauri, cargo)."""
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

    # The shipped notices file names the build it belongs to. A bump changes
    # no attribution, so restamp it here rather than failing the freshness gate.
    try:
        from gen_third_party_notices import restamp_version

        if restamp_version(v):
            changes.append(f"THIRD_PARTY_NOTICES.txt: -> {v}")
    except Exception as exc:
        print(f"WARNING: could not restamp third-party notices ({exc})")

    if changes:
        print(f"Synced version to {v}:")
        for c in changes:
            print(f"  {c}")
    else:
        print(f"Version {v} already synced across all configs.")

    return v


def check_third_party_notices() -> None:
    """Fail the build when a shipped dependency is missing from the notices."""
    script = ROOT / "scripts" / "gen_third_party_notices.py"
    if not script.exists():
        print("WARNING: gen_third_party_notices.py missing — notices not verified")
        return
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print("ERROR: third-party notices are out of date — regenerate before building.")
        sys.exit(1)


def core_library_name() -> str:
    """File name the loader and Tauri resources expect on this platform."""
    if sys.platform == "win32":
        return "remedy_core.dll"
    if sys.platform == "darwin":
        return "libremedy_core.dylib"
    return "libremedy_core.so"


def default_core_library_path() -> Path:
    """Where ``zig build -Doptimize=ReleaseSafe`` installs the shared library."""
    subdir = "bin" if sys.platform == "win32" else "lib"
    return ROOT / "native" / "zig" / "zig-out" / subdir / core_library_name()


def resolve_core_library(override: str | Path | None = None) -> Path:
    """The Zig core to stage, or exit with a message that says how to get one."""
    expected = core_library_name()
    path = Path(override).expanduser() if override else default_core_library_path()
    if not path.is_file():
        print(
            f"ERROR: native core library missing at {path}.\n"
            "       Build it with: cd native/zig && zig build -Doptimize=ReleaseSafe\n"
            f"       or pass --core-lib PATH pointing at a built {expected}."
        )
        sys.exit(1)
    if path.name != expected:
        print(
            f"ERROR: --core-lib must point at a file named {expected} "
            f"(got {path.name}); the Desktop resource only resolves that name."
        )
        sys.exit(1)
    try:
        import ctypes

        library = ctypes.CDLL(str(path))
        library.remedy_core_abi_version.argtypes = []
        library.remedy_core_abi_version.restype = ctypes.c_uint32
        abi = int(library.remedy_core_abi_version())
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {path} does not load as the Remedy native core: {exc}")
        sys.exit(1)
    if abi != REQUIRED_CORE_ABI:
        print(
            f"ERROR: {path} reports ABI {abi}; Desktop requires ABI "
            f"{REQUIRED_CORE_ABI}. Rebuild with: cd native/zig && "
            "zig build -Doptimize=ReleaseSafe"
        )
        sys.exit(1)
    return path.resolve()


def target_triple() -> str:
    """Rust target triple Tauri uses for externalBin (``remedy-runtime-<triple>``)."""
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


def runtime_bin_paths() -> tuple[Path, Path]:
    """Plain Go output and the Tauri-triple copy under ``desktop/bin``.

    Windows: ``remedy-runtime.exe`` + ``remedy-runtime-x86_64-pc-windows-msvc.exe``.
    Linux: ``remedy-runtime`` + ``remedy-runtime-x86_64-unknown-linux-gnu`` (no .exe).
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    triple = target_triple()
    return (
        DESKTOP_BIN / f"remedy-runtime{suffix}",
        DESKTOP_BIN / f"remedy-runtime-{triple}{suffix}",
    )


def staged_core_path() -> Path:
    """Where Tauri ``bundle.resources`` expects the Zig core."""
    return DESKTOP_BIN / core_library_name()


def _run(cmd: list[str], *, cwd: Path) -> None:
    print(f"Running: {' '.join(cmd)} (cwd={cwd})")
    subprocess.check_call(cmd, cwd=str(cwd))


def build_runtime() -> tuple[Path, Path]:
    """Compile Go ``remedy-runtime`` into ``desktop/bin`` with the Tauri triple copy."""
    plain, triple_path = runtime_bin_paths()
    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags",
            "-s -w",
            "-o",
            str(plain),
            "./cmd/remedy-runtime",
        ],
        cwd=GO_MOD,
    )
    if not plain.is_file():
        print(f"ERROR: go build produced no binary at {plain}")
        sys.exit(1)
    shutil.copy2(plain, triple_path)
    size_mb = plain.stat().st_size / (1024 * 1024)
    print(f"Runtime: {plain} ({size_mb:.1f} MB)")
    print(f"Sidecar: {triple_path}")
    return plain, triple_path


def build_zig_core() -> Path:
    """Build the Zig shared library into ``native/zig/zig-out``."""
    _run(["zig", "build", "-Doptimize=ReleaseSafe"], cwd=ZIG_DIR)
    built = default_core_library_path()
    if not built.is_file():
        print(f"ERROR: zig build produced no library at {built}")
        sys.exit(1)
    return built


def stage_rmdy_worker(*, build_if_missing: bool = True) -> Path:
    """Build/copy ``rmdy_tool_worker.pyz`` into ``desktop/bin`` for Tauri resources."""
    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)
    dest = DESKTOP_BIN / "rmdy_tool_worker.pyz"
    builder = ROOT / "scripts" / "build_rmdy_worker.py"
    if build_if_missing or not dest.is_file():
        _run([sys.executable, str(builder), "--out", str(dest)], cwd=ROOT)
    if not dest.is_file():
        print(f"ERROR: RMDY worker zipapp missing at {dest}")
        sys.exit(1)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"RMDY worker: {dest} ({size_mb:.1f} MB)")
    return dest


def stage_core(core_lib: str | Path | None = None, *, build_if_missing: bool = True) -> Path:
    """Copy the Zig core into ``desktop/bin`` for Tauri resources.

    Builds with zig only when the library is missing (pass ``--core-lib`` to
    stage a specific artifact without rebuilding).
    """
    path = Path(core_lib).expanduser() if core_lib else default_core_library_path()
    if not path.is_file():
        if core_lib or not build_if_missing:
            resolve_core_library(core_lib)  # exits with the missing-file message
        print(f"Native core missing at {path}; building with zig…")
        path = build_zig_core()
    resolved = resolve_core_library(path)
    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)
    dest = staged_core_path()
    shutil.copy2(resolved, dest)
    size_kb = dest.stat().st_size / 1024
    print(f"Core: {dest} ({size_kb:.1f} KB)")
    return dest


def build(
    cache_clean: bool = False,
    ci: bool = False,
    core_lib: str | None = None,
    skip_runtime: bool = False,
    skip_core: bool = False,
    skip_rmdy_worker: bool = False,
) -> None:
    """Build and stage remedy-runtime + remedy_core + RMDY pyz into ``desktop/bin``."""
    print(f"Staging Desktop native artifacts… (root={ROOT})")

    v = sync_versions()
    if ci:
        print(f"[CI] Stamped version {v} across manifests before build")
        expected = os.environ.get("REMEDY_RELEASE_VERSION", "").lstrip("v").strip()
        if expected and expected != v:
            print(
                f"ERROR: REMEDY_RELEASE_VERSION={expected} does not match "
                f"pyproject.toml version={v}. Bump with scripts/sync_version.py first."
            )
            sys.exit(1)

    check_third_party_notices()

    if cache_clean and DESKTOP_BIN.exists():
        shutil.rmtree(DESKTOP_BIN)
        print(f"Cleaned {DESKTOP_BIN}")
    DESKTOP_BIN.mkdir(parents=True, exist_ok=True)

    # Drop leftover legacy remedy-desktop binaries so a stale sidecar cannot
    # be mistaken for the packaged launch path.
    for stale in DESKTOP_BIN.glob("remedy-desktop*"):
        stale.unlink(missing_ok=True)
        print(f"Removed legacy sidecar: {stale.name}")

    if not skip_runtime:
        build_runtime()
    else:
        print("Skipping Go remedy-runtime (--skip-runtime)")

    if not skip_core:
        # --core-lib: stage the given file (no zig rebuild). Otherwise build.
        stage_core(core_lib, build_if_missing=core_lib is None)
    else:
        print("Skipping Zig remedy_core (--skip-core)")

    if not skip_rmdy_worker:
        stage_rmdy_worker(build_if_missing=True)
    else:
        print("Skipping RMDY worker zipapp (--skip-rmdy-worker)")

    print(f"\nStaged under {DESKTOP_BIN}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Stage Go remedy-runtime + Zig remedy_core for Desktop"
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Remove desktop/bin before staging",
    )
    p.add_argument(
        "--stage",
        action="store_true",
        help="Copy final NSIS installer to dist/ (after tauri build)",
    )
    p.add_argument(
        "--ci",
        action="store_true",
        help="CI mode — require REMEDY_RELEASE_VERSION match when set; still syncs versions",
    )
    p.add_argument(
        "--core-lib",
        metavar="PATH",
        help="Zig core library to stage (default: build native/zig ReleaseSafe)",
    )
    p.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Do not build Go remedy-runtime",
    )
    p.add_argument(
        "--skip-rmdy-worker",
        action="store_true",
        help="Do not build/stage rmdy_tool_worker.pyz",
    )
    p.add_argument(
        "--skip-core",
        action="store_true",
        help="Do not build/stage Zig remedy_core",
    )
    args = p.parse_args()

    build(
        cache_clean=args.clean,
        ci=args.ci,
        core_lib=args.core_lib,
        skip_runtime=args.skip_runtime,
        skip_core=args.skip_core,
        skip_rmdy_worker=args.skip_rmdy_worker,
    )

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

"""Build a Python-only RMDY tool worker package (no HTTP server).

Production :7400 is Go ``remedy-runtime``. This script packages
``python -m remedy.runtime.rmdy_tool_worker`` as:

* **zipapp** (default) — ``dist/rmdy_tool_worker.pyz`` runnable with
  ``python dist/rmdy_tool_worker.pyz`` (or a shebang interpreter).
* **PyInstaller onedir** (optional) — writes ``dist/rmdy_tool_worker.spec``
  and can invoke PyInstaller when ``--pyinstaller`` is passed. The spec
  excludes fastapi/uvicorn/starlette so the worker never bundles an HTTP
  server.

The zipapp holds ``src/remedy`` only. Its third-party closure (pydantic,
PyYAML and friends) ships beside it in a ``rmdy-deps/`` directory because
``pydantic_core``/``_yaml`` are compiled extensions and a zipapp cannot load a
``.pyd``/``.so`` from inside the archive. The Go launcher passes that directory
to the worker as ``REMEDY_RMDY_DEPS``; versions come from ``uv.lock`` and the
interpreter series from the managed CPython pinned in
``native/go/workers/managed_python.go``.

Usage::

    python scripts/build_rmdy_worker.py
    python scripts/build_rmdy_worker.py --out dist/rmdy_tool_worker.pyz
    python scripts/build_rmdy_worker.py --skip-deps       # zipapp only (offline)
    python scripts/build_rmdy_worker.py --deps-only       # deps directory only
    python scripts/build_rmdy_worker.py --write-spec
    python scripts/build_rmdy_worker.py --pyinstaller   # requires pyinstaller
    python scripts/build_rmdy_worker.py --check         # validate only
"""

from __future__ import annotations

import argparse
import compileall
import json
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_REMEDY = ROOT / "src" / "remedy"
DIST = ROOT / "dist"
DEFAULT_PYZ = DIST / "rmdy_tool_worker.pyz"
DEFAULT_SPEC = DIST / "rmdy_tool_worker.spec"
STAGE_DIRNAME = "_rmdy_worker_stage"
UV_LOCK = ROOT / "uv.lock"
MANAGED_PYTHON_GO = ROOT / "native" / "go" / "workers" / "managed_python.go"

# Third-party closure the worker needs to import ``remedy.runtime`` handlers
# and run ``prompt.assemble`` (the Go attach probe). Verified empirically: with
# these six on sys.path every RMDY handler module imports and prompt.assemble
# answers on a stdlib-only interpreter. Distribution names as spelled in
# uv.lock; the module names they provide are DEPS_IMPORT_NAMES.
DEPS_DISTRIBUTIONS = (
    "pydantic",
    "pydantic-core",
    "annotated-types",
    "typing-extensions",
    "typing-inspection",
    "pyyaml",
)

# ``tomllib`` is stdlib from 3.11; below that ``remedy.interfaces.config``
# falls back to tomli, which then has to ship too.
TOMLI_DISTRIBUTION = "tomli"

DEPS_IMPORT_NAMES = (
    "annotated_types",
    "pydantic",
    "pydantic_core",
    "typing_extensions",
    "typing_inspection",
    "yaml",
)

DEPS_DIRNAME = "rmdy-deps"
DEPS_MANIFEST_NAME = "rmdy_deps.json"

# HTTP server / ASGI stacks must never ship in the worker package.
# Go remedy-runtime owns :7400; FastAPI create_app is deleted from source.
BANNED_SERVER_MODULE_NAMES = frozenset(
    {
        "fastapi",
        "uvicorn",
        "starlette",
        "hypercorn",
        "daphne",
        "aiohttp_wsgi",
    }
)

# Paths under src/remedy to skip when staging (relative posix parts).
SKIP_NAME_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "tests",
    }
)

SKIP_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".dll", ".dylib"})

# Phase 6 absolute: only these top-level remedy entries may ship in the worker.
WORKER_ALLOW_TOP_LEVEL = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "home.py",
        "models.py",
        "assistant",
        "bundled_skills",
        "core",
        "credentials",
        "events",
        "execution",
        "i18n",
        "interfaces",
        "memory",
        "migrate",
        "nanoswarm",
        "policy",
        "runtime",
        "skills",
        "telephony",
        "tools",
        "verification",
        "vision",
        "voice",
    }
)

# Never ship agent tool-registration forests or MCP bridge in the worker package.
WORKER_DENY_REL_FILES = frozenset(
    {
        "core/agent_mcp_bridge.py",
    }
)

# Required worker surface (fail closed if missing after stage).
WORKER_REQUIRED_REL_FILES = frozenset(
    {
        "runtime/rmdy_tool_worker.py",
        "runtime/prompt_assemble.py",
        "runtime/voice_vision_rmdy.py",
        "core/web_helpers.py",
    }
)


def _is_denied_worker_path(rel: Path) -> bool:
    """True when *rel* (under src/remedy) must not ship in the worker package."""
    posix = rel.as_posix()
    if posix in WORKER_DENY_REL_FILES:
        return True
    name = rel.name
    return name.startswith("agent_") and name.endswith("_tools.py")


def _is_skipped(rel: Path) -> bool:
    if any(part in SKIP_NAME_PARTS for part in rel.parts):
        return True
    if rel.suffix.lower() in SKIP_SUFFIXES:
        return True
    # Never stage a top-level package named like a banned server module.
    if rel.parts and rel.parts[0].lower() in BANNED_SERVER_MODULE_NAMES:
        return True
    top = rel.parts[0] if rel.parts else ""
    if top and top not in WORKER_ALLOW_TOP_LEVEL:
        return True
    return bool(_is_denied_worker_path(rel))


def stage_worker_tree(dest: Path, *, source: Path = SRC_REMEDY) -> Path:
    """Copy allowlisted ``src/remedy`` → ``dest/remedy`` for the RMDY worker."""
    if not source.is_dir():
        raise FileNotFoundError(f"remedy package missing: {source}")
    package_root = dest / "remedy"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if _is_skipped(rel):
            continue
        target = package_root / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    main_py = dest / "__main__.py"
    main_py.write_text(
        "from remedy.runtime.rmdy_tool_worker import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    # Bundle pyproject so frozen/version resolution stays authoritative.
    src_pp = ROOT / "pyproject.toml"
    if src_pp.is_file():
        shutil.copy2(src_pp, dest / "pyproject.toml")
    return package_root


def assert_no_http_server_bundle(tree: Path) -> None:
    """Fail closed if a banned HTTP-server package path appears in the staged tree."""
    if not tree.is_dir():
        raise FileNotFoundError(tree)
    offenders: list[str] = []
    for path in tree.rglob("*"):
        parts_lower = {p.lower() for p in path.relative_to(tree).parts}
        if parts_lower & BANNED_SERVER_MODULE_NAMES:
            offenders.append(str(path.relative_to(tree)))
            continue
        name = path.name.lower()
        stem = path.stem.lower()
        if name in BANNED_SERVER_MODULE_NAMES or stem in BANNED_SERVER_MODULE_NAMES:
            offenders.append(str(path.relative_to(tree)))
    if offenders:
        preview = "\n  ".join(offenders[:20])
        raise RuntimeError(
            "worker package must not bundle an HTTP server; found:\n  " + preview
        )


def assert_worker_allowlist(tree: Path) -> None:
    """Fail closed if required worker files are missing or denied agent_* ships."""
    if not tree.is_dir():
        raise FileNotFoundError(tree)
    package = tree / "remedy" if (tree / "remedy").is_dir() else tree
    missing = [
        rel for rel in sorted(WORKER_REQUIRED_REL_FILES) if not (package / rel).is_file()
    ]
    if missing:
        raise RuntimeError(
            "worker package missing required files:\n  " + "\n  ".join(missing)
        )
    offenders: list[str] = []
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package)
        if _is_denied_worker_path(rel):
            offenders.append(rel.as_posix())
    if offenders:
        preview = "\n  ".join(offenders[:20])
        raise RuntimeError(
            "worker package must not include denied agent tool modules:\n  " + preview
        )


def locked_versions(distributions: tuple[str, ...], *, lock: Path = UV_LOCK) -> dict[str, str]:
    """Exact versions for *distributions* as resolved in ``uv.lock``."""
    if not lock.is_file():
        raise FileNotFoundError(f"uv.lock missing: {lock}")
    wanted = {name.lower(): name for name in distributions}
    found: dict[str, str] = {}
    for block in lock.read_text(encoding="utf-8").split("[[package]]"):
        name_match = re.search(r'^name = "(.+?)"', block, re.MULTILINE)
        version_match = re.search(r'^version = "(.+?)"', block, re.MULTILINE)
        if not name_match or not version_match:
            continue
        key = name_match.group(1).lower()
        if key in wanted:
            found[wanted[key]] = version_match.group(1)
    missing = [name for name in distributions if name not in found]
    if missing:
        raise RuntimeError(
            "uv.lock does not pin worker dependencies: " + ", ".join(missing)
        )
    return found


def managed_python_series(*, source: Path = MANAGED_PYTHON_GO) -> str:
    """``major.minor`` of the managed CPython the packaged worker runs on."""
    if not source.is_file():
        raise FileNotFoundError(f"managed python pin missing: {source}")
    match = re.search(r'pbsPy\s*=\s*"(\d+)\.(\d+)(?:\.\d+)?"', source.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"could not read pbsPy from {source}")
    return f"{match.group(1)}.{match.group(2)}"


def deps_distributions(series: str) -> tuple[str, ...]:
    """Distributions to stage for a worker interpreter of *series*."""
    major, minor = (int(part) for part in series.split(".", 1))
    if (major, minor) < (3, 11):
        return DEPS_DISTRIBUTIONS + (TOMLI_DISTRIBUTION,)
    return DEPS_DISTRIBUTIONS


def deps_requirements(series: str) -> list[str]:
    """``name==version`` pins for the worker dependency closure."""
    distributions = deps_distributions(series)
    versions = locked_versions(distributions)
    return [f"{name}=={versions[name]}" for name in distributions]


def _installer_argv() -> list[str]:
    """``pip install`` front end: uv when available, else this interpreter's pip.

    Release CI has both; the project ``.venv`` deliberately has no pip, so uv
    is tried first.
    """
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install"]
    try:
        import pip  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "no installer for the worker dependency directory: install uv, or "
            "run this script with an interpreter that has pip"
        ) from exc
    return [sys.executable, "-m", "pip", "install"]


def build_deps(
    dest: Path,
    *,
    series: str | None = None,
    python_platform: str | None = None,
) -> Path:
    """Install the pinned worker dependency closure into *dest*.

    Wheels only, no transitive resolution: every member of the closure is
    pinned from uv.lock, so ``--no-deps`` keeps the directory reproducible and
    free of anything the worker does not import. ``--python-version`` selects
    the managed CPython ABI (pydantic_core and PyYAML ship version-specific
    extension modules, not abi3).
    """
    series = series or managed_python_series()
    requirements = deps_requirements(series)
    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    argv = _installer_argv()
    argv += [
        "--target",
        str(dest),
        "--python-version",
        series,
        "--only-binary=:all:",
        "--no-deps",
        *requirements,
    ]
    if python_platform:
        flag = "--python-platform" if Path(argv[0]).stem == "uv" else "--platform"
        argv += [flag, python_platform]
    subprocess.run(argv, cwd=ROOT, check=True)

    prune_deps_tree(dest)
    write_deps_manifest(dest, series=series, requirements=requirements)
    assert_deps_complete(dest)
    return dest


def prune_deps_tree(dest: Path) -> None:
    """Drop console scripts and bytecode caches — the worker imports only."""
    for name in ("bin", "Scripts"):
        shutil.rmtree(dest / name, ignore_errors=True)
    for cache in dest.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def write_deps_manifest(dest: Path, *, series: str, requirements: list[str]) -> Path:
    """Record what was staged so the runtime and tests can verify it."""
    path = dest / DEPS_MANIFEST_NAME
    payload = {
        "python_version": series,
        "requirements": requirements,
        "import_names": list(DEPS_IMPORT_NAMES),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def assert_deps_complete(dest: Path) -> None:
    """Fail closed when a staged dependency directory is missing a module."""
    if not dest.is_dir():
        raise FileNotFoundError(dest)
    missing = [
        name
        for name in DEPS_IMPORT_NAMES
        if not (dest / name).is_dir() and not (dest / f"{name}.py").is_file()
    ]
    if missing:
        raise RuntimeError(
            "worker dependency directory is incomplete: " + ", ".join(missing)
        )
    extensions = list((dest / "pydantic_core").glob("_pydantic_core*"))
    if not extensions:
        raise RuntimeError(
            "pydantic_core extension module missing from "
            f"{dest} — the wheel for this interpreter did not install"
        )


def write_pyinstaller_spec(path: Path) -> Path:
    """Write an onedir PyInstaller spec that excludes HTTP server modules."""
    path.parent.mkdir(parents=True, exist_ok=True)
    excludes = ",\n    ".join(repr(m) for m in sorted(BANNED_SERVER_MODULE_NAMES))
    # Paths use forward slashes in the generated spec for cross-platform readability.
    src = str((ROOT / "src").resolve()).replace("\\", "/")
    root = str(ROOT.resolve()).replace("\\", "/")
    content = f'''# Auto-generated by scripts/build_rmdy_worker.py — RMDY worker onedir.
# Do not bundle fastapi/uvicorn; Go remedy-runtime owns :7400.
# Build: pyinstaller --noconfirm {path.name}
# Entry equivalent: python -m remedy.runtime.rmdy_tool_worker

block_cipher = None

a = Analysis(
    ["{src}/remedy/runtime/rmdy_tool_worker.py"],
    pathex=["{src}", "{root}"],
    binaries=[],
    datas=[("{root}/pyproject.toml", ".")],
    hiddenimports=[
        "remedy.runtime.rmdy_tool_worker",
        "remedy.runtime.prompt_assemble",
        "remedy.runtime.voice_vision_rmdy",
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
    {excludes},
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rmdy_tool_worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="rmdy_tool_worker",
)
'''
    path.write_text(content, encoding="utf-8")
    return path


def build_zipapp(
    out: Path,
    *,
    stage_parent: Path | None = None,
    compressed: bool = True,
) -> Path:
    """Stage the worker tree and write a zipapp at *out*."""
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    parent = stage_parent or (out.parent / STAGE_DIRNAME)
    if parent.exists():
        shutil.rmtree(parent)
    parent.mkdir(parents=True)
    package_root = stage_worker_tree(parent)
    assert_no_http_server_bundle(parent)
    assert_worker_allowlist(parent)
    # Bytecode compile soft-check: catch syntax errors before shipping.
    compileall.compile_dir(str(package_root), quiet=1, legacy=True)
    if out.exists():
        out.unlink()
    # __main__.py is written by stage_worker_tree; do not also pass main=.
    zipapp.create_archive(
        parent,
        out,
        interpreter=sys.executable,
        compressed=compressed,
    )
    # Ensure readable/executable bit on POSIX for the shebang path.
    mode = out.stat().st_mode
    out.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return out


def run_pyinstaller(spec: Path) -> None:
    try:
        import PyInstaller.__main__ as pi_main  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "PyInstaller is not installed. Write the spec with --write-spec and "
            "install pyinstaller in a venv, or use the default zipapp build."
        ) from exc
    pi_main.run(["--noconfirm", str(spec)])


def check_only() -> None:
    """Validate source entry + exclusion contract without writing artifacts."""
    entry = SRC_REMEDY / "runtime" / "rmdy_tool_worker.py"
    if not entry.is_file():
        raise SystemExit(f"missing worker entry: {entry}")
    py_compile.compile(str(entry), doraise=True)
    # Importability (dev tree on sys.path).
    sys.path.insert(0, str(ROOT / "src"))
    import importlib

    mod = importlib.import_module("remedy.runtime.rmdy_tool_worker")
    if not callable(getattr(mod, "main", None)):
        raise SystemExit("remedy.runtime.rmdy_tool_worker.main is not callable")
    for banned in BANNED_SERVER_MODULE_NAMES:
        if (SRC_REMEDY / banned).exists():
            raise SystemExit(f"banned server package present under src/remedy: {banned}")
    # Allowlist contract against a throwaway stage.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="rmdy-check-") as tmp:
        stage_worker_tree(Path(tmp))
        assert_no_http_server_bundle(Path(tmp))
        assert_worker_allowlist(Path(tmp))
    print(
        "check ok: entry importable; no HTTP server; worker allowlist/deny gate passed"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_rmdy_worker")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_PYZ,
        help=f"zipapp output path (default: {DEFAULT_PYZ})",
    )
    parser.add_argument(
        "--write-spec",
        action="store_true",
        help=f"write PyInstaller onedir spec to {DEFAULT_SPEC}",
    )
    parser.add_argument(
        "--spec-out",
        type=Path,
        default=DEFAULT_SPEC,
        help="path for --write-spec / --pyinstaller",
    )
    parser.add_argument(
        "--pyinstaller",
        action="store_true",
        help="write spec and run PyInstaller onedir build (optional dep)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate entry + no-server contract; do not build",
    )
    parser.add_argument(
        "--keep-stage",
        action="store_true",
        help="keep staging directory next to --out for inspection",
    )
    parser.add_argument(
        "--deps-out",
        type=Path,
        default=None,
        help=f"dependency directory (default: {DEPS_DIRNAME}/ beside --out)",
    )
    parser.add_argument(
        "--deps-python-version",
        default=None,
        metavar="MAJOR.MINOR",
        help="interpreter series to install wheels for (default: managed CPython pin)",
    )
    parser.add_argument(
        "--deps-platform",
        default=None,
        metavar="TAG",
        help="cross-build wheels for this platform tag (default: this machine)",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="build the zipapp only (offline); packaged installs need the deps",
    )
    parser.add_argument(
        "--deps-only",
        action="store_true",
        help="build the dependency directory only; do not write the zipapp",
    )
    args = parser.parse_args(argv)

    if args.check:
        check_only()
        return 0

    deps_out = args.deps_out or (args.out.resolve().parent / DEPS_DIRNAME)

    if args.deps_only:
        deps = build_deps(
            deps_out,
            series=args.deps_python_version,
            python_platform=args.deps_platform,
        )
        print(f"wrote worker dependencies: {deps}")
        return 0

    if args.write_spec or args.pyinstaller:
        spec = write_pyinstaller_spec(args.spec_out.resolve())
        print(f"wrote PyInstaller onedir spec: {spec}")
        print(
            "excludes HTTP server modules: "
            + ", ".join(sorted(BANNED_SERVER_MODULE_NAMES))
        )
        if args.pyinstaller:
            run_pyinstaller(spec)
            print("PyInstaller onedir build finished under dist/rmdy_tool_worker/")
            return 0
        if not args.pyinstaller and args.write_spec and args.out == DEFAULT_PYZ:
            # Spec-only request: still allow combining with zipapp if --out set differently.
            pass

    stage_parent = args.out.resolve().parent / STAGE_DIRNAME
    out = build_zipapp(args.out, stage_parent=stage_parent)
    print(f"wrote zipapp: {out}")
    print("run: python", out)
    print("entry: remedy.runtime.rmdy_tool_worker:main (no HTTP server)")
    if not args.keep_stage and stage_parent.exists():
        shutil.rmtree(stage_parent, ignore_errors=True)
    if args.skip_deps:
        print(f"skipped worker dependencies (--skip-deps); {DEPS_DIRNAME}/ not staged")
        return 0
    deps = build_deps(
        deps_out,
        series=args.deps_python_version,
        python_platform=args.deps_platform,
    )
    print(f"wrote worker dependencies: {deps}")
    print("launcher env: REMEDY_RMDY_DEPS=" + str(deps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Pre-push gate: this machine is Remedy's CI.

There is no CI on GitHub. One command runs the complete matrix (every
runtime, platform lane and check Remedy ships) against the exact tree being
pushed, on this machine, before the push leaves it. It is wired in as the git ``pre-push`` hook (`.githooks/pre-push`) and can
be run by hand.

    uv run python scripts/prepush.py                 # verify HEAD (full matrix)
    uv run python scripts/prepush.py --list          # print the matrix and exit
    uv run python scripts/prepush.py --serial        # one lane at a time
    uv run python scripts/prepush.py --release v0.51.0   # release gate only
    uv run python scripts/prepush.py --install       # point git at .githooks

Rules the hook enforces (see AGENTS.md "Local CI"):

* A push to ``master``/``main`` needs a clean working tree, HEAD equal to the
  commit being pushed, a fast-forward, and a green run of the full matrix on
  that tree. When everything that changed since a green ancestor is docs or
  tests (see ``lanes_for_paths``), only the lanes covering them run. Green runs are remembered per tree hash under ``.git`` so a
  re-push of an already verified tree does not repeat forty minutes of work.
* A push of a release tag ``v*`` needs the tagged commit already on the
  remote's master, every version surface equal to the tag, and a green run
  of this gate recorded for that commit's tree. GitHub only builds the two
  macOS bundles from the tag (``desktop-release.yml``); the release itself is
  published from this machine by ``scripts/release_local.py``, which checks
  the same record.
* Other branches are not gated.
* A non-fast-forward push to ``master`` is refused. The one exception is a
  history rewrite the owner asked for: ``REMEDY_PREPUSH_ALLOW_REWRITE=1``
  lets it through, and the tree still has to be green.

Nothing here touches the live Remedy home: every test lane runs with
``REMEDY_HOME`` pointed at a scratch directory.

Lane dependencies. The ``python`` lane runs library-backed tests against the
Zig core the ``native`` lane builds (``native/zig/zig-out``), so it declares
``requires=("native",)``: it runs after that lane succeeds, ``--only python``
pulls the native lane in, and ``REMEDY_NATIVE_CORE_LIB`` is exported to the
freshly built library. A missing library fails the lane rather than letting
those tests skip. The ``linux`` lane builds its own ``libremedy_core.so``
inside WSL (into ``/tmp``, never into the checkout's ``zig-out``) when WSL has
``zig``; without it the loader reports ``not-installed`` and the log says so.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = platform.system() == "Windows"
ZERO_SHA = "0" * 40
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+([.-].*)?$")
PROTECTED_BRANCHES = ("master", "main")
IMPORT_SMOKE = 'python -c "import remedy; print(\'import OK\')"'


# --------------------------------------------------------------------------- #
# The matrix. `tests/test_ci_matrix.py` pins every lane and command, so the
# gate cannot quietly lose a runtime, a platform or a check.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Step:
    name: str
    command: str
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    windows_command: str | None = None

    def shell_command(self) -> str:
        if IS_WINDOWS and self.windows_command is not None:
            return self.windows_command
        return self.command


@dataclass(frozen=True)
class Lane:
    """A group of steps that runs in one worker.

    ``requires`` names lanes whose output this lane consumes; such a lane runs
    after the parallel phase and only once every required lane is green.
    """

    key: str
    title: str
    steps: tuple[Step, ...]
    serial_after_others: bool = False
    requires: tuple[str, ...] = ()


def native_core_library_path() -> Path:
    """Where the native lane's ``zig build -Doptimize=ReleaseSafe`` installs the core."""
    if IS_WINDOWS:
        return ROOT / "native" / "zig" / "zig-out" / "bin" / "remedy_core.dll"
    if platform.system() == "Darwin":
        return ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.dylib"
    return ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.so"


# Sentinel commands resolved at run time (see _run_lane).
WSL_PYTEST = "__wsl_pytest__"
WSL_ZIG_BUILD = "__wsl_zig_build__"
WSL_GO_TEST = "__wsl_go_test__"
WSL_CARGO = "__wsl_cargo__"
REQUIRE_NATIVE_CORE = "__require_native_core__"
NATIVE_CORE_ENV = {"REMEDY_NATIVE_CORE_LIB": str(native_core_library_path())}
WSL_ZIG_PREFIX = "/tmp/remedy-prepush-zig"
WSL_NATIVE_CORE_LIB = f"{WSL_ZIG_PREFIX}/lib/libremedy_core.so"
NATIVE_CORE_HEADER = ROOT / "native" / "zig" / "include" / "remedy_core.h"


def required_native_abi(*, header: Path = NATIVE_CORE_HEADER) -> int:
    """``REMEDY_CORE_ABI_VERSION`` from the C header — the one source of truth.

    The ABI number is mirrored in the Zig core, the Go loader, the Python
    loader and this gate; parsing the header means a bump lands in one place
    and every consumer follows instead of silently drifting.
    """
    match = re.search(
        r"^#define\s+REMEDY_CORE_ABI_VERSION\s+(\d+)u?\s*$",
        header.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit(f"cannot read REMEDY_CORE_ABI_VERSION from {header}")
    return int(match.group(1))


REQUIRED_NATIVE_ABI = required_native_abi()


RUST_ENV = {
    # Rust desktop shell alone: compile and test, warnings are errors.
    "TAURI_CONFIG": '{"bundle":{"active":false,"externalBin":[],"resources":null}}',
    "RUSTFLAGS": "-D warnings",
}
RUST_WSL_ENV = {
    **RUST_ENV,
    # TAURI_CONFIG is JSON with double quotes; do not embed it in bash -lc.
    "WSLENV": "RUSTFLAGS/u:TAURI_CONFIG/u",
}

CHECKS = Lane(
    "checks",
    "Sanitize, lint, types, docs, import smoke",
    (
        Step("sanitize", "uv run python scripts/check_sanitize.py"),
        Step("ruff", "uv run ruff check . --no-fix"),
        Step("mypy", "uv run mypy"),
        Step("mypy exclude may only shrink", "uv run python scripts/check_mypy_exclude.py"),
        Step("import smoke", "uv run " + IMPORT_SMOKE),
        Step("docs sync", "uv run python scripts/check_docs.py"),
    ),
)

PYTHON = Lane(
    "python",
    "Full pytest suite (this OS) + wheel",
    (
        Step("native core present", REQUIRE_NATIVE_CORE),
        Step(
            "pytest",
            "uv run pytest -q --tb=short -p no:cacheprovider",
            env=NATIVE_CORE_ENV,
        ),
        Step("uv build", "uv build"),
    ),
    # Timing-sensitive tests (telephony frame pacing) must not share the box
    # with cargo and gradle; this lane runs after the parallel lanes finish.
    serial_after_others=True,
    # Library-backed tests load the core the native lane just built.
    requires=("native",),
)

LINUX = Lane(
    "linux",
    "Linux CI surface under WSL (Go native + pytest + cargo)",
    (
        Step("zig build (linux)", WSL_ZIG_BUILD),
        # native-core (ubuntu) — Windows go test alone misses filepath.ToSlash traps.
        Step("go test (linux)", WSL_GO_TEST),
        Step("pytest (linux)", WSL_PYTEST),
        # After pytest so WSL cargo does not share DrvFS with Windows go test.
        Step("cargo test+check (linux)", WSL_CARGO, env=RUST_WSL_ENV),
    ),
)

DESKTOP = Lane(
    "desktop",
    "React SPA tests + production build",
    (
        Step("npm test", "npm test", cwd="desktop"),
        Step("npm run build", "npm run build", cwd="desktop"),
    ),
)

RUST = Lane(
    "rust",
    "Tauri shell (cargo)",
    (
        Step("cargo test", "cargo test --locked", cwd="desktop/src-tauri", env=RUST_ENV),
        Step("cargo test (iroh sidecar)", "cargo test --locked", cwd="native/iroh", env=RUST_ENV),
    ),
)

NATIVE = Lane(
    "native",
    "Go + Zig native runtime",
    (
        Step("go test", "go test ./...", cwd="native/go"),
        Step("go race", "go test -race ./...", cwd="native/go"),
        Step("go vet", "go vet ./...", cwd="native/go"),
        Step("language boundaries", "go run ./cmd/check-boundaries -root ..", cwd="native/go"),
        Step(
            "benchmarks",
            "go test ./benchmarks -run NONE -bench . -benchtime 20x -benchmem "
            "> ../benchmarks/latest-local.txt",
            cwd="native/go",
        ),
        Step(
            "benchcheck",
            "go run ./cmd/benchcheck -budgets ../benchmarks/budgets.json "
            "-input ../benchmarks/latest-local.txt",
            cwd="native/go",
        ),
        Step("zig test", "zig build test", cwd="native/zig"),
        Step("zig test release-safe", "zig build test -Doptimize=ReleaseSafe", cwd="native/zig"),
        Step("zig build release-safe", "zig build -Doptimize=ReleaseSafe", cwd="native/zig"),
        # SHA-NI / host-ISA bake must not ship (0.62.1 connecting hang on Comet Lake).
        Step(
            "remedy_core ISA baseline",
            "uv run python scripts/check_remedy_core_isa.py",
        ),
    ),
)

ANDROID = Lane(
    "android",
    "RemedyConnect Android (gradle)",
    (
        Step(
            "gradle",
            "./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease",
            cwd="android",
            windows_command=(
                r".\gradlew.bat --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease"
            ),
        ),
    ),
)

LANES: tuple[Lane, ...] = (CHECKS, LINUX, DESKTOP, RUST, NATIVE, ANDROID, PYTHON)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_dir() -> Path:
    out = _git("rev-parse", "--git-common-dir")
    path = Path(out)
    return path if path.is_absolute() else ROOT / path


def _state_dir() -> Path:
    d = _git_dir() / "remedy-prepush"
    (d / "logs").mkdir(parents=True, exist_ok=True)
    (d / "verified").mkdir(parents=True, exist_ok=True)
    return d


def _tree_of(commit: str) -> str:
    return _git("rev-parse", f"{commit}^{{tree}}")


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def unreleased_section(changelog: str) -> str:
    """The body under CHANGELOG.md's ``## [Unreleased]`` heading, stripped."""
    m = re.search(
        r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def _require_no_unreleased_notes(commit: str) -> None:
    """The public repo never mentions unreleased work.

    Pending notes live in the clone-only ``docs/UNRELEASED.md`` and are folded
    under the version heading in the release commit.
    """
    body = unreleased_section(_git("show", f"{commit}:CHANGELOG.md", check=False))
    if body:
        first = body.splitlines()[0]
        _fail(
            "CHANGELOG.md has text under [Unreleased]; the public repo never mentions "
            "unreleased work. Move it to docs/UNRELEASED.md (clone-only) and fold it "
            f"under the version heading in the release commit. First line: {first!r}"
        )


def _say(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # A Windows console in a legacy code page cannot print lane logs
        # (vitest's ✓ / ×). Losing a glyph must never hide the gate's result.
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


def _fail(msg: str) -> None:
    _say(f"\nprepush: REFUSED: {msg}\n")
    raise SystemExit(1)


def _wsl_path(path: Path) -> str:
    drive, rest = os.path.splitdrive(str(path.resolve()))
    return f"/mnt/{drive[0].lower()}{rest.replace(os.sep, '/')}"


@functools.cache
def _wsl_has_zig() -> bool:
    """Whether a login shell inside WSL can find ``zig``."""
    if not IS_WINDOWS or shutil.which("wsl") is None:
        return False
    proc = subprocess.run(
        ["wsl", "-e", "bash", "-lc", "command -v zig"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


@functools.cache
def _wsl_has_go() -> bool:
    """Whether a login shell inside WSL can find ``go``."""
    if not IS_WINDOWS or shutil.which("wsl") is None:
        return False
    proc = subprocess.run(
        ["wsl", "-e", "bash", "-lc", "command -v go"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


@functools.cache
def _wsl_has_cargo() -> bool:
    """Whether a login shell inside WSL can find ``cargo``."""
    if not IS_WINDOWS or shutil.which("wsl") is None:
        return False
    proc = subprocess.run(
        ["wsl", "-e", "bash", "-lc", "command -v cargo"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _wsl_zig_abi_assert() -> str:
    """Shell snippet: load the WSL-installed .so and refuse a stale ABI.

    Path is ``sys.argv[1]`` so the snippet needs no nested quotes inside
    ``bash -lc "..."`` under Windows ``cmd.exe`` (``shell=True``).
    """
    return (
        "python3 -c "
        "'import ctypes,sys;"
        "lib=ctypes.CDLL(sys.argv[1]);"
        "v=int(lib.remedy_core_abi_version());"
        f"assert v=={REQUIRED_NATIVE_ABI},v' "
        f"{WSL_NATIVE_CORE_LIB}"
    )


def _wsl_zig_build_command() -> str | None:
    """Build the Linux core inside WSL, installed under /tmp so the Windows
    native lane's ``zig-out`` and ``.zig-cache`` (running in parallel) are
    never touched.

    Also drops any checkout-local ``libremedy_core.so`` (same soname would make
    Linux dlopen reuse a stale handle) and asserts ``REQUIRED_NATIVE_ABI`` on
    the /tmp install. On ABI mismatch, wipe the WSL zig caches and rebuild
    once — DrvFS mtimes can leave a cached older ABI after a bump.

    Returns None on a non-Windows host; "" when WSL is missing.
    """
    if not IS_WINDOWS:
        return None
    if shutil.which("wsl") is None:
        return ""
    checkout_so = _wsl_path(ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.so")
    build = (
        f"zig build -Doptimize=ReleaseSafe --prefix {WSL_ZIG_PREFIX} "
        f"--cache-dir {WSL_ZIG_PREFIX}-cache --global-cache-dir {WSL_ZIG_PREFIX}-global"
    )
    assert_abi = _wsl_zig_abi_assert()
    inner = (
        f"rm -f {checkout_so} && "
        f"cd {_wsl_path(ROOT / 'native' / 'zig')} && "
        f"{build} && test -f {WSL_NATIVE_CORE_LIB} && "
        f"({assert_abi} || ("
        f"echo 'prepush: WSL remedy_core ABI mismatch — wiping zig caches and rebuilding' && "
        f"rm -rf {WSL_ZIG_PREFIX}-cache {WSL_ZIG_PREFIX}-global && "
        f"{build} && test -f {WSL_NATIVE_CORE_LIB} && {assert_abi}))"
    )
    return f'wsl -e bash -lc "{inner}"'


def _wsl_pytest_command() -> str | None:
    """The Linux CI pytest step, reproduced under WSL from this checkout.

    Points the loader at the library ``_wsl_zig_build_command`` installed when
    WSL has zig. Returns None on a non-Windows host (the `python` lane already
    is Linux).
    """
    if not IS_WINDOWS:
        return None
    if shutil.which("wsl") is None:
        return ""
    core = f"REMEDY_NATIVE_CORE_LIB={WSL_NATIVE_CORE_LIB} " if _wsl_has_zig() else ""
    # Drop checkout-local .so again so a parallel Windows zig-out write cannot
    # win the soname race against the /tmp install during pytest.
    checkout_so = _wsl_path(ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.so")
    # GitHub ubuntu-latest has no session display. WSLg often sets DISPLAY, which
    # hides headless AT-SPI / dbus aborts that kill CI (pytest exit 133). Match CI.
    # Strip */node_modules/.bin from PATH so a Windows-built esbuild on /mnt/c
    # cannot masquerade as the ubuntu-latest toolchain.
    #
    # No double quotes inside ``inner``: the outer form is ``bash -lc "..."`` under
    # Windows ``cmd.exe`` (``shell=True``). Nested ``"`` closes early and runs
    # ``tr`` / ``grep`` as cmd builtins ("'tr' is not recognized").
    path_scrub = (
        "IFS=:; _rp=; for _d in $PATH; do "
        "case $_d in */node_modules/.bin) ;; *) _rp=${_rp:+$_rp:}$_d;; esac; "
        "done; export PATH=$_rp; unset IFS _rp _d"
    )
    inner = (
        f"rm -f {checkout_so} && "
        f"cd {_wsl_path(ROOT)} && mkdir -p /tmp/remedy-prepush-home && "
        "unset DISPLAY WAYLAND_DISPLAY && "
        f"{path_scrub} && "
        "REMEDY_HOME=/tmp/remedy-prepush-home "
        # Python 3.13 here, 3.12 in the Windows `python` lane: both supported
        # versions run every push with no extra lane time.
        "UV_PROJECT_ENVIRONMENT=/tmp/remedy-prepush-venv313 "
        f"{core}"
        "uv run --python 3.13 pytest -q --tb=short -p no:cacheprovider"
    )
    return f'wsl -e bash -lc "{inner}"'


def _wsl_go_test_command() -> str | None:
    """``native-core`` Linux ``go test ./...``, from this checkout under WSL.

    Windows ``go test`` alone misses filepath separator traps that fail on
    ubuntu-latest in ~30s. Returns None on a non-Windows host; "" without WSL.
    """
    if not IS_WINDOWS:
        return None
    if shutil.which("wsl") is None:
        return ""
    go_dir = _wsl_path(ROOT / "native" / "go")
    inner = f"cd {go_dir} && go test ./..."
    return f'wsl -e bash -lc "{inner}"'


def _wsl_cargo_command() -> str | None:
    """``rust-desktop`` Linux ``cargo test/check --locked``, under WSL.

    Windows cargo does not type-check ``cfg(not(target_os = "windows"))``
    unused bindings; ubuntu-22.04 CI does, with ``RUSTFLAGS=-D warnings``.
    Target dir is ``/tmp`` so this does not share ``desktop/src-tauri/target``
    with the Windows cargo steps. Returns None on a Linux host; "" without WSL.
    """
    if not IS_WINDOWS:
        return None
    if shutil.which("wsl") is None:
        return ""
    tauri = _wsl_path(ROOT / "desktop" / "src-tauri")
    inner = (
        f"cd {tauri} && "
        "export CARGO_TARGET_DIR=/tmp/remedy-prepush-cargo && "
        "cargo test --locked"
    )
    return f'wsl -e bash -lc "{inner}"'


# --------------------------------------------------------------------------- #
# Running the matrix
# --------------------------------------------------------------------------- #


@dataclass
class LaneResult:
    lane: Lane
    ok: bool
    seconds: float
    failed_step: str | None = None
    log: Path | None = None
    skipped: str | None = None


def _run_lane(lane: Lane, scratch_home: Path, log_dir: Path) -> LaneResult:
    started = time.monotonic()
    log = log_dir / f"{lane.key}.log"
    base_env = dict(os.environ)
    base_env["REMEDY_HOME"] = str(scratch_home)
    base_env["CI"] = "1"
    base_env.setdefault("PYTHONIOENCODING", "utf-8")
    with log.open("w", encoding="utf-8") as fh:
        for step in lane.steps:
            command = step.shell_command()
            if command == REQUIRE_NATIVE_CORE:
                library = native_core_library_path()
                if not library.is_file():
                    fh.write(
                        f"native core library missing at {library}; the native lane "
                        "builds it (cd native/zig && zig build -Doptimize=ReleaseSafe). "
                        "Library-backed tests would skip, so this lane refuses to run.\n"
                    )
                    return LaneResult(lane, False, time.monotonic() - started, step.name, log)
                fh.write(f"native core: {library}\n")
                continue
            if command in (WSL_PYTEST, WSL_ZIG_BUILD, WSL_GO_TEST, WSL_CARGO):
                if command == WSL_PYTEST:
                    resolved = _wsl_pytest_command()
                elif command == WSL_ZIG_BUILD:
                    resolved = _wsl_zig_build_command()
                elif command == WSL_GO_TEST:
                    resolved = _wsl_go_test_command()
                else:
                    resolved = _wsl_cargo_command()
                if resolved is None:
                    fh.write("skipped: host is already Linux\n")
                    continue
                if resolved == "":
                    fh.write("WSL is not installed; Linux CI surface cannot run here\n")
                    return LaneResult(
                        lane, False, time.monotonic() - started, step.name, log
                    )
                if command == WSL_ZIG_BUILD and not _wsl_has_zig():
                    fh.write(
                        "zig is not installed inside WSL; Linux pytest runs without the "
                        "native core (the loader reports not-installed). Install zig in "
                        "WSL to exercise library loading there.\n"
                    )
                    continue
                if command == WSL_GO_TEST and not _wsl_has_go():
                    fh.write(
                        "go is not installed inside WSL; refusing to skip Linux go test "
                        "(native-core ubuntu CI would catch filepath traps Windows misses).\n"
                    )
                    return LaneResult(
                        lane, False, time.monotonic() - started, step.name, log
                    )
                if command == WSL_CARGO and not _wsl_has_cargo():
                    fh.write(
                        "cargo is not installed inside WSL; refusing to skip Linux cargo "
                        "(ubuntu rust-desktop -D warnings catches cfg(not windows) unused).\n"
                    )
                    return LaneResult(
                        lane, False, time.monotonic() - started, step.name, log
                    )
                command = resolved
            fh.write(f"\n=== {step.name}: {command}  (cwd={step.cwd})\n")
            fh.flush()
            proc = subprocess.run(
                command,
                cwd=ROOT / step.cwd,
                env={**base_env, **step.env},
                shell=True,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
            if proc.returncode != 0:
                fh.write(f"\n!!! {step.name} exited {proc.returncode}\n")
                return LaneResult(lane, False, time.monotonic() - started, step.name, log)
    return LaneResult(lane, True, time.monotonic() - started, None, log)


def _tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def run_matrix(lanes: Iterable[Lane], *, serial: bool) -> list[LaneResult]:
    lanes = list(lanes)
    state = _state_dir()
    log_dir = state / "logs"
    scratch_home = Path(tempfile.mkdtemp(prefix="remedy-prepush-home-"))
    results: list[LaneResult] = []
    lock = threading.Lock()

    def worker(lane: Lane) -> None:
        result = _run_lane(lane, scratch_home, log_dir)
        with lock:
            results.append(result)
            mark = "ok  " if result.ok else "FAIL"
            _say(f"  [{mark}] {lane.key:8} {lane.title}  ({result.seconds / 60:.1f} min)")

    first = [ln for ln in lanes if ln.key == "checks"]
    parallel = [
        ln for ln in lanes if ln.key != "checks" and not ln.serial_after_others and not ln.requires
    ]
    last = [ln for ln in lanes if ln.serial_after_others or ln.requires]

    def unmet_requirement(lane: Lane) -> str | None:
        done = {r.lane.key: r for r in results}
        for key in lane.requires:
            if key not in done:
                return f"required lane '{key}' did not run"
            if not done[key].ok:
                return f"required lane '{key}' failed"
        return None

    def run_last(lane: Lane) -> bool:
        """Run a dependent lane; False when it was skipped or failed."""
        unmet = unmet_requirement(lane)
        if unmet:
            _say(f"  [skip] {lane.key:8} {lane.title}  ({unmet})")
            return False
        worker(lane)
        return results[-1].ok

    for lane in first:
        worker(lane)
        if not results[-1].ok:
            return results

    if serial:
        for lane in parallel:
            worker(lane)
            if not results[-1].ok:
                return results
        for lane in last:
            if not run_last(lane):
                return results
        return results

    threads = [threading.Thread(target=worker, args=(lane,), daemon=True) for lane in parallel]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if all(r.ok for r in results):
        for lane in last:
            run_last(lane)
    return results


def with_required_lanes(keys: set[str]) -> set[str]:
    """Close a lane selection over ``Lane.requires`` (``--only python`` adds native)."""
    wanted = set(keys)
    while True:
        pulled = {req for ln in LANES if ln.key in wanted for req in ln.requires} - wanted
        if not pulled:
            return wanted
        for ln in LANES:
            if ln.key in wanted and set(ln.requires) & pulled:
                _say(f"prepush: {ln.key} requires {', '.join(ln.requires)}; adding")
        wanted |= pulled


# --------------------------------------------------------------------------- #
# Reduced gate for test- and docs-only changes (owner decision, 2026-11)
# --------------------------------------------------------------------------- #

ALL_LANE_KEYS = frozenset({"checks", "linux", "desktop", "rust", "native", "android", "python"})


def lanes_for_paths(paths: list[str]) -> set[str] | None:
    """Lanes that cover a change made only of docs and tests, or None when any
    path needs the full matrix.

    Only files that cannot change what ships qualify: Markdown, Python tests,
    desktop ``*.test.ts(x)``, Go ``*_test.go`` and Android unit tests. The
    checks lane (sanitize, lint, types, docs) always runs. Anything else —
    app code, scripts, workflows, build or config files, test fixtures that
    other stacks read — needs every lane.
    """
    lanes = {"checks"}
    if not paths:
        return None
    for raw in paths:
        p = raw.replace("\\", "/").strip()
        if not p:
            continue
        if p.endswith(".md"):
            # Markdown that ships: help articles are bundled into the SPA,
            # bundled skills are read by the Python runtime and its tests.
            if p.startswith("desktop/"):
                lanes.add("desktop")
            elif p.startswith("src/"):
                lanes |= {"linux", "python"}
            continue
        if p.startswith("tests/") and p.endswith(".py"):
            lanes |= {"linux", "python"}
        elif p.startswith("desktop/src/") and re.search(r"\.test\.tsx?$", p):
            lanes.add("desktop")
        elif p.startswith("native/go/") and p.endswith("_test.go"):
            lanes |= {"native", "linux"}
        elif p.startswith("android/") and "/src/test/" in p:
            lanes.add("android")
        else:
            return None
    return lanes


def last_green_ancestor(commit: str, *, depth: int = 50) -> str | None:
    """The newest ancestor of `commit` whose tree this gate recorded green."""
    for ancestor in _git("rev-list", f"--max-count={depth}", commit, check=False).split()[1:]:
        if verified_green(ancestor):
            return ancestor
    return None


def verify_tree(commit: str, *, serial: bool, force: bool, only: set[str] | None) -> bool:
    tree = _tree_of(commit)
    stamp = _state_dir() / "verified" / tree
    if stamp.exists() and not force and only is None:
        info = json.loads(stamp.read_text(encoding="utf-8"))
        _say(
            f"prepush: tree {tree[:12]} already verified green "
            f"({info.get('when', '?')}, {info.get('minutes', '?')} min). "
            "Use --force to rerun."
        )
        return True

    reduced_from: str | None = None
    if only is None and not force:
        base = last_green_ancestor(commit)
        if base:
            changed = _git("diff", "--name-only", base, commit, check=False).splitlines()
            needed = lanes_for_paths(changed)
            if needed is not None and needed != ALL_LANE_KEYS:
                only = needed
                reduced_from = base
                _say(
                    f"prepush: only docs/tests changed since green {base[:12]} "
                    f"({len(changed)} file(s)); running {', '.join(sorted(needed))}. "
                    "Use --force for the full matrix."
                )

    if only is not None:
        only = with_required_lanes(only)
    lanes = LANES if only is None else tuple(ln for ln in LANES if ln.key in only)
    _say(f"prepush: verifying tree {tree[:12]} (commit {commit[:12]})")
    _say(f"  lanes: {', '.join(ln.key for ln in lanes)}  logs: {_state_dir() / 'logs'}")
    started = time.monotonic()
    results = run_matrix(lanes, serial=serial)
    minutes = round((time.monotonic() - started) / 60, 1)

    failed = [r for r in results if not r.ok]
    ran = {r.lane.key for r in results}
    missing = [ln.key for ln in lanes if ln.key not in ran]
    if failed or missing:
        _say("")
        for r in failed:
            _say(f"prepush: {r.lane.key} failed at step '{r.failed_step}'. Last lines of {r.log}:")
            _say(_tail(r.log) if r.log else "")
            _say("")
        if missing:
            _say(f"prepush: lanes not run after the failure: {', '.join(missing)}")
        _say(f"prepush: RED after {minutes} min. Fix it here; do not push.")
        return False

    # A hand-picked --only run never stamps; a full run or a reduced run
    # (docs/tests on top of a green tree) does.
    if only is None or reduced_from is not None:
        record: dict[str, object] = {
            "commit": commit,
            "tree": tree,
            "when": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "host": platform.node(),
            "minutes": minutes,
        }
        if reduced_from is not None:
            record["reduced_from"] = reduced_from
            record["lanes"] = sorted(ln.key for ln in lanes)
        stamp.write_text(json.dumps(record), encoding="utf-8")
    _say(f"prepush: GREEN after {minutes} min.")
    return True


# --------------------------------------------------------------------------- #
# Release gate
# --------------------------------------------------------------------------- #


def verified_green(commit: str) -> bool:
    """True when this gate has recorded a green full-matrix run for the tree
    `commit` points at. The record lives under ``.git/remedy-prepush``."""
    tree = _tree_of(commit)
    return bool(tree) and (_state_dir() / "verified" / tree).is_file()


def check_release(tag: str, remote: str = "origin") -> None:
    if not RELEASE_TAG_RE.match(tag):
        _fail(f"'{tag}' is not a release tag (expected v<major>.<minor>.<patch>)")
    version = tag[1:]
    sha = _git("rev-parse", f"{tag}^{{commit}}", check=False)
    if not sha:
        _fail(f"tag {tag} does not exist locally")

    _say(f"prepush: release gate for {tag} -> {sha[:12]}")

    pyproject = _pyproject_version()
    if pyproject != version:
        _fail(f"pyproject.toml is {pyproject}, tag says {version}; run scripts/sync_version.py")
    if _tree_of(sha) != _tree_of("HEAD"):
        _fail(f"{tag} points at a different tree than HEAD; check the tag out first")
    aligned = subprocess.run(
        ["uv", "run", "python", "scripts/sync_version.py", "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if aligned.returncode != 0:
        _fail("version surfaces are not aligned:\n" + aligned.stdout.strip())
    _say(f"  [ok] every version surface reads {version}")

    _git("fetch", "--quiet", remote, "master")
    on_master = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, f"{remote}/master"], cwd=ROOT
    ).returncode
    if on_master != 0:
        _fail(f"{tag} is not on {remote}/master yet; push master first")
    _say(f"  [ok] {tag} is on {remote}/master")

    if not verified_green(sha):
        _fail(
            f"no green run of this gate is recorded for {sha[:12]}; "
            "run `uv run python scripts/prepush.py` on that commit first"
        )
    _say(f"  [ok] full matrix verified green on this machine for {sha[:12]}")
    _say("prepush: release gate passed.")


# --------------------------------------------------------------------------- #
# Hook protocol
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PushRef:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def _parse_hook_stdin(lines: Iterable[str]) -> list[PushRef]:
    refs = []
    for line in lines:
        parts = line.split()
        if len(parts) == 4:
            refs.append(PushRef(*parts))
    return refs


def _require_clean_tree() -> None:
    dirty = _git("status", "--porcelain", "--untracked-files=normal")
    if dirty:
        _fail(
            "the working tree is not clean, so the pushed commit cannot be tested as-is. "
            "Commit or discard these first:\n" + dirty
        )


def hook(remote: str, refs: list[PushRef], *, serial: bool) -> None:
    head = _git("rev-parse", "HEAD")
    for ref in refs:
        if ref.local_sha == ZERO_SHA:
            _say(f"prepush: deleting {ref.remote_ref} on {remote}; nothing to verify")
            continue

        if ref.remote_ref.startswith("refs/tags/"):
            tag = ref.remote_ref.removeprefix("refs/tags/")
            if RELEASE_TAG_RE.match(tag):
                check_release(tag, remote)
            else:
                _say(f"prepush: tag {tag} is not a release tag; not gated")
            continue

        branch = ref.remote_ref.removeprefix("refs/heads/")
        if branch not in PROTECTED_BRANCHES:
            _say(f"prepush: {branch} is not a protected branch; not gated (CI does not run there)")
            continue

        if ref.remote_sha != ZERO_SHA:
            ff = subprocess.run(
                ["git", "merge-base", "--is-ancestor", ref.remote_sha, ref.local_sha], cwd=ROOT
            ).returncode
            if ff != 0 and os.environ.get("REMEDY_PREPUSH_ALLOW_REWRITE") != "1":
                _fail(
                    f"push to {remote}/{branch} is not a fast-forward. Never force-push "
                    "the public branch; fetch, rebase, and verify again. (Only when the "
                    "owner explicitly asks for a history rewrite: "
                    "REMEDY_PREPUSH_ALLOW_REWRITE=1, and the tree must still be green.)"
                )
            if ff != 0:
                _say(f"prepush: WARNING rewriting {remote}/{branch} history on owner request")
        if ref.local_sha != head:
            _fail(
                f"{ref.local_ref} ({ref.local_sha[:12]}) is not the checked-out HEAD "
                f"({head[:12]}); check it out so the matrix runs on the tree being pushed"
            )
        _require_clean_tree()
        _require_no_unreleased_notes(ref.local_sha)
        if not verify_tree(ref.local_sha, serial=serial, force=False, only=None):
            raise SystemExit(1)
        _say(f"prepush: {remote}/{branch} <- {ref.local_sha[:12]} allowed")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def install_hook() -> None:
    _git("config", "core.hooksPath", ".githooks")
    _say("prepush: core.hooksPath = .githooks (pre-push gate active for this clone)")


def print_matrix() -> None:
    for lane in LANES:
        suffix = f"  (after: {', '.join(lane.requires)})" if lane.requires else ""
        _say(f"{lane.key}: {lane.title}{suffix}")
        for step in lane.steps:
            cmd = step.shell_command()
            if cmd == WSL_PYTEST:
                cmd = _wsl_pytest_command() or "(host is Linux: covered by the python lane)"
            elif cmd == WSL_ZIG_BUILD:
                if not IS_WINDOWS:
                    cmd = "(host is Linux: covered by the native lane)"
                elif not _wsl_has_zig():
                    cmd = "(zig not installed in WSL: loader reports not-installed)"
                else:
                    cmd = _wsl_zig_build_command() or ""
            elif cmd == WSL_CARGO:
                if not IS_WINDOWS:
                    cmd = "(host is Linux: covered by cargo test/check)"
                elif not _wsl_has_cargo():
                    cmd = "(cargo not installed in WSL: lane fails)"
                else:
                    cmd = _wsl_cargo_command() or ""
            elif cmd == REQUIRE_NATIVE_CORE:
                cmd = f"require {native_core_library_path()}"
            env = " ".join(f"{k}={v}" for k, v in step.env.items() if k == "REMEDY_NATIVE_CORE_LIB")
            _say(f"    {step.name:28} [{step.cwd}] {cmd}{'  ' + env if env else ''}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hook", action="store_true", help="git pre-push hook mode (refs on stdin)")
    parser.add_argument("remote", nargs="?", default="origin")
    parser.add_argument("url", nargs="?", default="")
    parser.add_argument("--list", action="store_true", help="print the matrix and exit")
    parser.add_argument("--install", action="store_true", help="enable the hook for this clone")
    parser.add_argument("--release", metavar="TAG", help="run only the release gate for TAG")
    parser.add_argument("--serial", action="store_true", help="run lanes one at a time")
    parser.add_argument("--force", action="store_true", help="rerun even if this tree is stamped green")
    parser.add_argument(
        "--only",
        action="append",
        choices=[ln.key for ln in LANES],
        help="run a subset of lanes (never stamps the tree as verified)",
    )
    args = parser.parse_args(argv)

    if args.list:
        print_matrix()
        return 0
    if args.install:
        install_hook()
        return 0
    if args.release:
        check_release(args.release, args.remote)
        return 0
    if args.hook:
        refs = _parse_hook_stdin(sys.stdin.read().splitlines())
        if not refs:
            return 0
        hook(args.remote, refs, serial=args.serial)
        return 0

    _require_clean_tree()
    head = _git("rev-parse", "HEAD")
    _require_no_unreleased_notes(head)
    ok = verify_tree(
        head, serial=args.serial, force=args.force, only=set(args.only) if args.only else None
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

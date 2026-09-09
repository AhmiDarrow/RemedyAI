"""Pre-push gate: the public repo never receives a commit that CI would fail.

One command runs the complete public CI matrix (`.github/workflows/ci.yml`)
against the exact tree being pushed, on this machine, before the push leaves
it. It is wired in as the git ``pre-push`` hook (`.githooks/pre-push`) and can
be run by hand.

    uv run python scripts/prepush.py                 # verify HEAD (full matrix)
    uv run python scripts/prepush.py --list          # print the matrix and exit
    uv run python scripts/prepush.py --serial        # one lane at a time
    uv run python scripts/prepush.py --release v0.51.0   # release gate only
    uv run python scripts/prepush.py --install       # point git at .githooks

Rules the hook enforces (see AGENTS.md "Local CI"):

* A push to ``master``/``main`` needs a clean working tree, HEAD equal to the
  commit being pushed, a fast-forward, and a green run of the full matrix on
  that tree. Green runs are remembered per tree hash under ``.git`` so a
  re-push of an already verified tree does not repeat forty minutes of work.
* A push of a release tag ``v*`` needs the tagged commit already on the
  remote's master, every version surface equal to the tag, and a completed,
  successful public CI run for that commit. The release workflow checks the
  same three things again on GitHub before it builds anything.
* Other branches are not gated (public CI does not run on them).
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
# The matrix. `tests/test_ci_matrix.py` compares this with ci.yml so the local
# gate and public CI cannot drift apart.
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
    # Mirrors ci.yml rust-desktop: compile/test Rust alone, warnings are errors.
    "TAURI_CONFIG": '{"bundle":{"active":false,"externalBin":[],"resources":null}}',
    "RUSTFLAGS": "-D warnings",
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
    "Linux CI surface under WSL (Go native + pytest)",
    (
        Step("zig build (linux)", WSL_ZIG_BUILD),
        # native-core (ubuntu) — Windows go test alone misses filepath.ToSlash traps.
        Step("go test (linux)", WSL_GO_TEST),
        Step("pytest (linux)", WSL_PYTEST),
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
        Step("cargo check", "cargo check --locked", cwd="desktop/src-tauri", env=RUST_ENV),
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
    print(msg, flush=True)


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
        "UV_PROJECT_ENVIRONMENT=/tmp/remedy-prepush-venv "
        f"{core}"
        "uv run pytest -q --tb=short -p no:cacheprovider"
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
            if command in (WSL_PYTEST, WSL_ZIG_BUILD, WSL_GO_TEST):
                if command == WSL_PYTEST:
                    resolved = _wsl_pytest_command()
                elif command == WSL_ZIG_BUILD:
                    resolved = _wsl_zig_build_command()
                else:
                    resolved = _wsl_go_test_command()
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

    if only is None:
        stamp.write_text(
            json.dumps(
                {
                    "commit": commit,
                    "tree": tree,
                    "when": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "host": platform.node(),
                    "minutes": minutes,
                }
            ),
            encoding="utf-8",
        )
    _say(f"prepush: GREEN after {minutes} min.")
    return True


# --------------------------------------------------------------------------- #
# Release gate
# --------------------------------------------------------------------------- #


def _ci_conclusion(sha: str) -> tuple[str, str]:
    """(state, detail) for the public CI workflow on `sha` via the gh CLI."""
    if shutil.which("gh") is None:
        return "unknown", "the gh CLI is not installed, so CI status cannot be read"
    proc = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=20",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return "unknown", proc.stderr.strip() or "gh api failed"
    try:
        runs = json.loads(proc.stdout).get("workflow_runs", [])
    except json.JSONDecodeError:
        return "unknown", "gh api returned no JSON"
    if not runs:
        return "none", "public CI has not run for this commit"
    latest = runs[0]
    status, conclusion = latest.get("status"), latest.get("conclusion")
    url = latest.get("html_url", "")
    if status != "completed":
        return "running", f"CI run is {status}: {url}"
    if conclusion == "success":
        return "success", url
    return "failed", f"CI run concluded {conclusion}: {url}"


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
        _fail(f"{tag} is not on {remote}/master yet; push master first and wait for CI")
    _say(f"  [ok] {tag} is on {remote}/master")

    state, detail = _ci_conclusion(sha)
    if state != "success":
        _fail(f"public CI is not green for {sha[:12]}: {detail}")
    _say(f"  [ok] public CI green: {detail}")
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

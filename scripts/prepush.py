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

Nothing here touches the live Remedy home: every test lane runs with
``REMEDY_HOME`` pointed at a scratch directory.
"""

from __future__ import annotations

import argparse
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
IMPORT_SMOKE = (
    'python -c "import remedy; from remedy.interfaces.api import create_app; '
    "create_app(); print('import OK')\""
)


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
    """A group of steps that runs in one worker. Lanes are independent."""

    key: str
    title: str
    steps: tuple[Step, ...]
    serial_after_others: bool = False


RUST_ENV = {
    # Mirrors ci.yml rust-desktop: compile/test Rust alone, warnings are errors.
    "TAURI_CONFIG": '{"bundle":{"active":false,"externalBin":[],"resources":null}}',
    "RUSTFLAGS": "-D warnings",
}

CHECKS = Lane(
    "checks",
    "Lint, types, docs, import smoke",
    (
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
        Step("pytest", "uv run pytest -q --tb=short -p no:cacheprovider"),
        Step("uv build", "uv build"),
    ),
    # Timing-sensitive tests (telephony frame pacing) must not share the box
    # with cargo and gradle; this lane runs after the parallel lanes finish.
    serial_after_others=True,
)

LINUX = Lane(
    "linux",
    "Full pytest suite on Linux (WSL)",
    (Step("pytest (linux)", "__wsl_pytest__"),),
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


def _say(msg: str) -> None:
    print(msg, flush=True)


def _fail(msg: str) -> None:
    _say(f"\nprepush: REFUSED: {msg}\n")
    raise SystemExit(1)


def _wsl_path(path: Path) -> str:
    drive, rest = os.path.splitdrive(str(path.resolve()))
    return f"/mnt/{drive[0].lower()}{rest.replace(os.sep, '/')}"


def _wsl_pytest_command() -> str | None:
    """The Linux CI pytest step, reproduced under WSL from this checkout.

    Returns None on a non-Windows host (the `python` lane already is Linux).
    """
    if not IS_WINDOWS:
        return None
    if shutil.which("wsl") is None:
        return ""
    inner = (
        f"cd {_wsl_path(ROOT)} && mkdir -p /tmp/remedy-prepush-home && "
        "REMEDY_HOME=/tmp/remedy-prepush-home "
        "UV_PROJECT_ENVIRONMENT=/tmp/remedy-prepush-venv "
        "uv run pytest -q --tb=short -p no:cacheprovider"
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
            if command == "__wsl_pytest__":
                resolved = _wsl_pytest_command()
                if resolved is None:
                    fh.write("skipped: host is already Linux\n")
                    continue
                if resolved == "":
                    fh.write("WSL is not installed; Linux pytest cannot run here\n")
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
    parallel = [ln for ln in lanes if ln.key != "checks" and not ln.serial_after_others]
    last = [ln for ln in lanes if ln.serial_after_others]

    for lane in first:
        worker(lane)
        if not results[-1].ok:
            return results

    if serial:
        for lane in parallel + last:
            worker(lane)
            if not results[-1].ok:
                return results
        return results

    threads = [threading.Thread(target=worker, args=(lane,), daemon=True) for lane in parallel]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if all(r.ok for r in results):
        for lane in last:
            worker(lane)
    return results


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
            if ff != 0:
                _fail(
                    f"push to {remote}/{branch} is not a fast-forward. Never force-push "
                    "the public branch; fetch, rebase, and verify again."
                )
        if ref.local_sha != head:
            _fail(
                f"{ref.local_ref} ({ref.local_sha[:12]}) is not the checked-out HEAD "
                f"({head[:12]}); check it out so the matrix runs on the tree being pushed"
            )
        _require_clean_tree()
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
        _say(f"{lane.key}: {lane.title}")
        for step in lane.steps:
            cmd = step.shell_command()
            if cmd == "__wsl_pytest__":
                cmd = _wsl_pytest_command() or "(host is Linux: covered by the python lane)"
            _say(f"    {step.name:28} [{step.cwd}] {cmd}")


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
    ok = verify_tree(
        head, serial=args.serial, force=args.force, only=set(args.only) if args.only else None
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

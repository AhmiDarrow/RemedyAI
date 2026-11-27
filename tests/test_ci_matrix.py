"""Keep the local CI gate and the release pipeline aligned with what Remedy ships.

There is no CI on GitHub: scripts/prepush.py runs the full matrix on the owner's
machine, GitHub only builds and signs release packages (desktop-release.yml),
and scripts/release_local.py publishes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow_jobs(name: str) -> dict[str, object]:
    payload = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text("utf-8"))
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: object) -> list[dict[str, object]]:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _run_commands(job: object) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _step_index(job: object, needle: str) -> int:
    """Index of the first step whose ``run`` contains ``needle``."""
    for index, step in enumerate(_steps(job)):
        if needle in str(step.get("run", "")):
            return index
    raise AssertionError(f"no step runs {needle!r}")


def _uses_step(job: object, action: str) -> dict[str, object]:
    for step in _steps(job):
        if str(step.get("uses", "")).startswith(action + "@"):
            return step
    raise AssertionError(f"no step uses {action}")


ZIG_BUILD = "zig build -Doptimize=ReleaseSafe"


def test_local_gate_covers_every_shipped_runtime_and_artifact() -> None:
    """Every runtime, platform and check Remedy ships runs on every push."""
    prepush = _prepush_module()
    assert {lane.key for lane in prepush.LANES} == {
        "checks", "linux", "desktop", "rust", "remedyui", "connect", "native", "python",
    }
    local = "\n".join(step.command for lane in prepush.LANES for step in lane.steps)
    for command in (
        "check_sanitize.py",
        "ruff check . --no-fix",
        "uv run mypy",
        "check_mypy_exclude.py",
        "import remedy",
        "check_docs.py",
        "pytest -q --tb=short",
        "uv build",
        "npm test",
        "npm run build",
        "cargo check",
        "cargo test --locked",
        "go test ./...",
        "go test -race ./...",
        "go vet ./...",
        "check-boundaries",
        "benchcheck",
        "zig build test",
        "zig build test -Doptimize=ReleaseSafe",
        "zig build -Doptimize=ReleaseSafe",
        "check_remedy_core_isa.py",
    ):
        assert command in local, f"scripts/prepush.py lost {command!r}"

    for lane in (prepush.REMEDYUI, prepush.CONNECT):
        for step in lane.steps:
            assert step.env["CARGO_BUILD_JOBS"], "cargo lanes cap their build jobs"
    assert [s.command for s in prepush.REMEDYUI.steps] == ["cargo test"]
    assert [s.command for s in prepush.CONNECT.steps] == ["cargo check"]
    assert prepush.REMEDYUI.steps[0].cwd == "desktop/remedyui"
    assert prepush.CONNECT.steps[0].cwd == "connect/app"

    # Only crates that ship: the old Tauri shell is not one of them.
    assert [s.cwd for s in prepush.RUST.steps] == ["connect/core", "native/iroh"]
    for step in prepush.RUST.steps:
        assert step.env["RUSTFLAGS"] == "-D warnings", "Rust warnings are errors"
        config = step.env["TAURI_CONFIG"]
        assert '"active":false' in config
        assert '"externalBin":[]' in config
        assert '"resources":null' in config


def test_release_builds_both_desktop_operating_systems_and_native_cores() -> None:
    jobs = _workflow_jobs("desktop-release.yml")
    assert {"build-runtime", "build-desktop", "build-runtime-linux", "build-desktop-linux"} <= jobs.keys()

    windows = _run_commands(jobs["build-runtime"]) + _run_commands(jobs["build-desktop"])
    assert "remedy-runtime.exe" in windows
    assert "remedy-runtime-x86_64-pc-windows-msvc.exe" in windows
    assert "remedy_core.dll" in windows
    assert "check_remedy_core_isa.py" in windows
    assert "package-windows.ps1" in windows

    linux = _run_commands(jobs["build-runtime-linux"]) + _run_commands(jobs["build-desktop-linux"])
    assert "remedy-runtime" in linux
    assert "remedy-runtime-x86_64-unknown-linux-gnu" in linux
    assert "libremedy_core.so" in linux
    assert "check_remedy_core_isa.py" in linux
    assert "package-linux.sh" in linux


def test_release_ships_no_python_worker() -> None:
    """The runtime starts no Python: no release job builds or bundles a worker."""
    text = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
    assert "rmdy" not in text, "the release workflow still builds or uploads the Python worker"
    for script in ("package-windows.ps1", "package-linux.sh", "package-macos.sh"):
        packaging = (ROOT / "desktop" / "remedyui" / "packaging" / script).read_text(encoding="utf-8")
        assert "rmdy" not in packaging, f"{script} still bundles the Python worker"


def test_zig_build_defaults_to_baseline_cpu() -> None:
    """Shipped remedy_core must not bake the CI host ISA (SHA-NI hang on Comet Lake)."""
    text = (ROOT / "native" / "zig" / "build.zig").read_text(encoding="utf-8")
    assert "cpu_model = .baseline" in text
    assert "SHA-NI" in text or "sha256msg1" in text


def test_linux_release_installs_zig_host_libs_before_building_remedy_core() -> None:
    """GitHub's Ubuntu runners have no X11/AT-SPI; Zig links them
    (native/zig/build.zig). Every Linux release job that builds Zig runs
    scripts/ci_install_linux_zig_host_deps.sh first."""
    script = (ROOT / "scripts" / "ci_install_linux_zig_host_deps.sh").read_text("utf-8")
    for pkg in (
        "libx11-dev",
        "libxtst-dev",
        "libatspi2.0-dev",
        "libdbus-1-dev",
        "libglib2.0-dev",
    ):
        assert pkg in script, f"host-deps script missing {pkg}"

    install = "ci_install_linux_zig_host_deps.sh"
    release = _workflow_jobs("desktop-release.yml")
    for name in ("build-runtime-linux", "build-runtime-linux-arm"):
        runs = _run_commands(release[name])
        assert install in runs, f"{name} must install Linux Zig host libs"
        assert runs.index(install) < runs.index(ZIG_BUILD), f"{name} must install host libs before zig build"


def test_prepush_linux_pytest_matches_headless_ci_env() -> None:
    """WSLg DISPLAY and Windows node_modules bins must not hide CI paths."""
    import sys

    import scripts.prepush as prepush

    source = (ROOT / "scripts" / "prepush.py").read_text(encoding="utf-8")
    assert "unset DISPLAY WAYLAND_DISPLAY" in source
    assert "node_modules/.bin" in source
    if sys.platform.startswith("win"):
        cmd = prepush._wsl_pytest_command()
        assert cmd, "Windows checkout must produce a WSL pytest command"
        assert cmd.startswith('wsl -e bash -lc "') and cmd.endswith('"')
        inner = cmd[len('wsl -e bash -lc "') : -1]
        assert '"' not in inner, (
            "nested quotes inside bash -lc break cmd.exe shell=True "
            "(tr/grep get run as Windows commands)"
        )
        assert "unset DISPLAY WAYLAND_DISPLAY" in inner
        assert "node_modules/.bin" in inner
        assert "for _d in $PATH" in inner


def test_release_builds_runtime_and_core_without_python_sidecar() -> None:
    """Packaged Desktop is remedy-runtime + Zig core; no PyInstaller sidecar."""
    jobs = _workflow_jobs("desktop-release.yml")
    for name, runtime, core in (
        ("build-runtime", "remedy-runtime.exe", "remedy_core.dll"),
        ("build-runtime-linux", "remedy-runtime", "libremedy_core.so"),
    ):
        job = jobs[name]
        runs = _run_commands(job)
        assert "go build" in runs, f"{name}: builds Go remedy-runtime"
        assert ZIG_BUILD in runs, f"{name}: builds Zig core"
        assert runtime in runs, f"{name}: produces {runtime}"
        assert core in runs, f"{name}: ships {core}"
        assert "build_desktop.py" not in runs, f"{name}: must not build remedy-desktop"
        assert "remedy-desktop" not in runs, f"{name}: must not reference remedy-desktop"


def _prepush_module():
    import importlib.util
    import sys

    path = ROOT / "scripts" / "prepush.py"
    spec = importlib.util.spec_from_file_location("remedy_prepush", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def test_prepush_reproduces_linux_from_a_windows_checkout() -> None:
    """The Linux suite, Linux cargo and Linux go test run from this checkout
    under WSL, and pytest never shares the box with cargo."""
    prepush = _prepush_module()
    assert any(step.command == "__wsl_cargo__" for step in prepush.LINUX.steps), (
        "Windows cargo alone misses Linux cfg(not windows) unused vars; WSL cargo is required"
    )
    assert any(step.command == "__wsl_pytest__" for step in prepush.LINUX.steps)
    assert any(step.command == "__wsl_go_test__" for step in prepush.LINUX.steps), (
        "Windows go test alone misses Linux filepath traps; WSL go test is required"
    )
    assert prepush.PYTHON.serial_after_others, "pytest must not share the box with cargo"


def test_prepush_hook_is_wired_and_executable() -> None:
    import subprocess

    hook = ROOT / ".githooks" / "pre-push"
    text = hook.read_text("utf-8")
    assert text.startswith("#!/bin/sh")
    assert "scripts/prepush.py --hook" in text
    listed = subprocess.run(
        ["git", "ls-files", "-s", ".githooks/pre-push"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    if listed:
        assert listed.startswith("100755"), "pre-push must be tracked executable"


def test_prepush_protects_only_public_branches_and_release_tags() -> None:
    prepush = _prepush_module()
    assert set(prepush.PROTECTED_BRANCHES) == {"master", "main"}
    assert prepush.RELEASE_TAG_RE.match("v0.50.2")
    assert prepush.RELEASE_TAG_RE.match("v1.2.3-rc.1")
    assert not prepush.RELEASE_TAG_RE.match("archive/v0.41.5")
    assert not prepush.RELEASE_TAG_RE.match("0.50.2")


def test_release_builds_nothing_before_the_commit_is_verified() -> None:
    """GitHub builds only from a master commit whose versions read the tag;
    publishing needs this machine's green record for that commit."""
    jobs = _workflow_jobs("desktop-release.yml")
    gate = _run_commands(jobs["verify"])
    assert "sync_version.py check" in gate, "release must check version surfaces"
    assert "merge-base --is-ancestor" in gate, "release must come from master"
    for name, job in jobs.items():
        if name == "verify":
            continue
        assert isinstance(job, dict)
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else needs
        assert needs and "verify" in needs, f"{name} must need verify"

    prepush = _prepush_module()
    source = (ROOT / "scripts" / "prepush.py").read_text("utf-8")
    check = source[source.index("def check_release(") :]
    assert "verified_green(sha)" in check, "a release tag needs the local green record"
    assert "actions/workflows" not in source, "the gate must not depend on GitHub CI"
    assert callable(prepush.verified_green)
    release_local = (ROOT / "scripts" / "release_local.py").read_text("utf-8")
    assert "prepush.check_release(tag)" in release_local, "publishing reruns the release gate"


def test_github_runs_no_ci_and_publishes_nothing() -> None:
    """There is no CI on GitHub, and no workflow there creates or edits a release."""
    workflows = ROOT / ".github" / "workflows"
    names = sorted(p.name for p in workflows.glob("*.yml"))
    assert names == [
        "desktop-release.yml",
        "remedyui-desktop-package.yml",
        "verify-release-assets.yml",
    ], names
    for path in workflows.glob("*.yml"):
        text = path.read_text("utf-8")
        for job in yaml.safe_load(text)["jobs"].values():
            for step in _steps(job):
                assert "action-gh-release" not in str(step.get("uses", "")), f"{path.name} must not publish"
                run = str(step.get("run", ""))
                assert "gh release create" not in run and "gh release edit" not in run
                assert "gh release upload" not in run, f"{path.name} must not publish"
        assert "pull_request" not in text, f"{path.name} must not run CI on pull requests"
    release = yaml.safe_load((workflows / "desktop-release.yml").read_text("utf-8"))
    triggers = release.get(True) or release.get("on")
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"tags": ["v*"]}, "builds only on release tags"
    package = yaml.safe_load((workflows / "remedyui-desktop-package.yml").read_text("utf-8"))
    package_triggers = package.get(True) or package.get("on")
    assert set(package_triggers) == {"workflow_dispatch"}, "the RemedyUI package runs only by hand"
    assert package.get("permissions") == {"contents": "read"}, "the RemedyUI package is read-only"


def test_both_supported_python_versions_run_every_push() -> None:
    """Windows runs the suite on 3.12 (.python-version), Linux on 3.13."""
    prepush = _prepush_module()
    assert (ROOT / ".python-version").read_text("utf-8").strip() == "3.12"
    assert 'requires-python = ">=3.12"' in (ROOT / "pyproject.toml").read_text("utf-8")
    source = (ROOT / "scripts" / "prepush.py").read_text("utf-8")
    assert "uv run --python 3.13 pytest" in source
    if prepush.IS_WINDOWS and prepush.shutil.which("wsl"):
        assert "--python 3.13" in (prepush._wsl_pytest_command() or "")


def test_public_changelog_never_carries_unreleased_notes() -> None:
    """The public repo never mentions unreleased work: [Unreleased] stays empty."""
    prepush = _prepush_module()
    assert prepush.unreleased_section("## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n- x\n") == ""
    assert prepush.unreleased_section("## [Unreleased]\n\n### New thing\n- y\n\n## [0.1.0]\n") == "### New thing\n- y"
    assert prepush.unreleased_section("# Changelog\n\n## [0.1.0]\n- x\n") == ""
    assert prepush.unreleased_section("## [Unreleased]\n\n- only entry at the end\n") != ""

    committed = (ROOT / "CHANGELOG.md").read_text("utf-8")
    assert prepush.unreleased_section(committed) == "", "keep pending notes in docs/UNRELEASED.md"


def test_experimental_versions_publish_as_prereleases() -> None:
    """A version whose CHANGELOG section says Experimental never becomes GitHub's
    "latest" release, so installed apps do not auto-update to it."""
    rl = _release_local_module()
    log = "## [1.2.0] - x\n- Experimental voice\n\n## [1.1.0] - y\n- stable\n"
    assert rl.is_prerelease(log, "1.2.0") is True
    assert rl.is_prerelease(log, "1.1.0") is False
    assert rl.is_prerelease(log, "9.9.9") is False
    source = (ROOT / "scripts" / "release_local.py").read_text("utf-8")
    main = source[source.index('f"v{version}", "-R", repo') :]
    assert 'if prerelease:\n        cmd += ["--prerelease", "--latest=false"]' in main


def _release_local_module():
    import importlib.util
    import sys

    path = ROOT / "scripts" / "release_local.py"
    spec = importlib.util.spec_from_file_location("remedy_release_local", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _good_sets(rl, ver: str = "1.2.0"):
    main = [
        f"Remedy.Desktop_{ver}_x64-setup.exe",
        "latest.json",
        f"Remedy.Desktop_{ver}_amd64.AppImage",
        f"Remedy.Desktop_{ver}_amd64.deb",
        f"Remedy.Desktop_{ver}_aarch64.dmg",
        f"Remedy.Desktop_{ver}_x64.dmg",
    ]
    runtime = [
        "remedy-native-SHA256SUMS.txt",
        "remedy-voice-female.wav",
        "remedy-voice-male.wav",
        "remedy-runtime-aarch64-apple-darwin",
        f"Remedy.Desktop_{ver}_aarch64.app.tar.gz",
        f"Remedy.Desktop_{ver}_x64.app.tar.gz",
        f"Remedy.Desktop_{ver}_aarch64.app.tar.gz.sig",
        f"Remedy.Desktop_{ver}_x64.app.tar.gz.sig",
    ]
    sums = "aa  remedy-voice-female.wav\nbb  remedy-runtime-aarch64-apple-darwin\n"
    darwin = {
        "darwin-aarch64": {"url_name": f"Remedy.Desktop_{ver}_aarch64.app.tar.gz", "signature": "s"},
        "darwin-x86_64": {"url_name": f"Remedy.Desktop_{ver}_x64.app.tar.gz", "signature": "s"},
    }
    manifest = rl.build_latest_json(
        version=ver,
        installer_asset=main[0],
        windows_sig="w",
        appimage=main[2],
        linux_sig="l",
        darwin=darwin,
        pub_date="2026-01-01T00:00:00Z",
    )
    return main, runtime, sums, manifest


def test_runtime_assets_publish_on_a_companion_prerelease() -> None:
    """The main release lists only installers + latest.json; pip runtime files,
    macOS updater tarballs and signatures go to runtime-v<ver>, which never
    takes over Latest and is published first. A dry run publishes nothing."""
    rl = _release_local_module()
    main, runtime, sums, manifest = _good_sets(rl)
    assert rl.check_release_sets("1.2.0", main, runtime, sums, manifest) == []

    # macOS updater links point at the companion release; Windows/Linux at the main one.
    platforms = manifest["platforms"]
    assert "/download/runtime-v1.2.0/" in platforms["darwin-aarch64"]["url"]
    assert "/download/v1.2.0/" in platforms["windows-x86_64"]["url"]

    # The family of broken release sets each fails with a reason.
    assert rl.check_release_sets("1.2.0", main + ["x.sig"], runtime, sums, manifest)
    assert rl.check_release_sets("1.2.0", main[:-1], runtime, sums, manifest)
    assert rl.check_release_sets("1.2.0", main, runtime[1:], sums, manifest)
    assert rl.check_release_sets("1.2.0", main, runtime, sums + "cc  not-staged\n", manifest)
    unsigned = {**manifest, "platforms": {**platforms, "windows-x86_64": {**platforms["windows-x86_64"], "signature": ""}}}
    assert rl.check_release_sets("1.2.0", main, runtime, sums, unsigned)
    import pytest

    with pytest.raises(rl.ReleaseError):
        rl.build_latest_json(
            version="1.2.0", installer_asset="a.exe", windows_sig="w", appimage="",
            linux_sig="", darwin={}, pub_date="x",
        )

    source = (ROOT / "scripts" / "release_local.py").read_text("utf-8")
    publish = source[source.index("def publish_releases(") : source.index("def build_wheel(")]
    assert publish.index('f"runtime-v{version}"') < publish.index('f"v{version}", "-R", repo'), (
        "companion release exists before Latest moves"
    )
    assert '"--prerelease", "--latest=false"' in publish[: publish.index('f"v{version}", "-R", repo')]
    notes = rl.main_notes("1.2.0", False)
    assert "Which file do I need?" in notes and "right-click" in notes and "pip install remedy-ai" in notes
    assert "Experimental pre-release" in rl.main_notes("1.2.0", True)
    run_main = source[source.index("def main(") :]
    assert "if not args.dry_run:\n            publish_releases(" in run_main
    assert "upload=not args.dry_run" in run_main

    # Published companion releases are re-checked on native macOS / ARM runners.
    verify = yaml.safe_load((ROOT / ".github" / "workflows" / "verify-release-assets.yml").read_text("utf-8"))
    triggers = verify.get(True) or verify.get("on")
    assert triggers == {"release": {"types": ["published"]}}
    job = verify["jobs"]["verify-native-assets"]
    assert "runtime-v" in str(job.get("if", ""))
    assert "release.tag_name" in str(job["steps"][1]["env"]["TAG"])


def test_updater_signatures_stay_raw_minisign() -> None:
    """latest.json carries raw minisign text, whether Tauri wrapped it in base64 or not."""
    import base64

    rl = _release_local_module()
    raw = "untrusted comment: signature from tauri secret key\nRWQ...\ntrusted comment: t\nAAA\n"
    assert rl.unwrap_sig(base64.b64encode(raw.encode()).decode()) == raw
    assert rl.unwrap_sig(raw) == raw.strip()
    assert rl.unwrap_sig("") == ""
    assert rl.safe_asset_name("Remedy Desktop_1.2.0_x64-setup.exe") == "Remedy.Desktop_1.2.0_x64-setup.exe"


def test_reduced_gate_runs_only_the_lanes_docs_and_tests_need() -> None:
    """Docs- and test-only changes on top of a green tree run the checks lane
    plus the lanes that execute them; anything that can change what ships
    needs the full matrix."""
    prepush = _prepush_module()
    lanes = prepush.lanes_for_paths
    assert lanes(["README.md", "docs/manual/22-grove.md"]) == {"checks"}
    assert lanes(["desktop/src/help/articles/22-grove.md"]) == {"checks", "desktop"}
    assert lanes(["src/remedy/bundled_skills/x/SKILL.md"]) == {"checks", "linux", "python"}
    assert lanes(["tests/test_ci_matrix.py"]) == {"checks", "linux", "python"}
    assert lanes(["desktop/src/grove/head.test.ts"]) == {"checks", "desktop"}
    assert lanes(["desktop/src/components/X.test.tsx"]) == {"checks", "desktop"}
    assert lanes(["native/go/httpapi/partner_head_test.go"]) == {"checks", "native", "linux"}
    assert lanes(["desktop/remedyui/help/articles/22-grove.md"]) == {"checks", "remedyui"}
    assert lanes(["tests\\test_x.py"]) == {"checks", "linux", "python"}  # Windows separators

    # Anything that ships, builds, or is read by another stack: full matrix.
    for path in (
        "scripts/prepush.py",
        "src/remedy/core/web_helpers.py",
        "desktop/src/grove/GroveApp.tsx",
        "desktop/src/grove/head.test.ts.snap",
        "native/go/httpapi/partner_head.go",
        "connect/app/src/lib.rs",
        ".github/workflows/desktop-release.yml",
        "pyproject.toml",
        "tests/fixtures/connect/noise_ik_snow.json",
        "desktop/package.json",
    ):
        assert lanes([path]) is None, path
    assert lanes(["tests/test_x.py", "src/remedy/core/a.py"]) is None, "one shipping file widens to full"
    assert lanes([]) is None, "no diff means nothing to reduce from"
    assert set(prepush.ALL_LANE_KEYS) == {lane.key for lane in prepush.LANES}

    source = (ROOT / "scripts" / "prepush.py").read_text("utf-8")
    verify = source[source.index("def verify_tree(") :]
    assert "if only is None and not force:" in verify, "--force always runs the full matrix"
    assert 'record["reduced_from"] = reduced_from' in verify, "reduced runs say so in the record"
    assert "if only is None or reduced_from is not None:" in verify, "hand-picked --only never stamps"

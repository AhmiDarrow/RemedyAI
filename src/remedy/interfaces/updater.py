"""Self-update mechanism for Remedy.

Handles:
- Git-clone installs (git pull + pip install -e .)
- pip installs (pip install --upgrade)
- Version checking against PyPI
- Post-update verification

Usage:
    remedy update            # check + apply
    remedy update --check    # check only
"""

from __future__ import annotations

import contextlib
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as importlib_version
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from remedy.execution.process import run_hidden

console = Console()

# PyPI distribution is remedy-ai (import package remains `remedy`).
# Never use pypi.org/pypi/remedy — that is an unrelated occupied name.
PYPI_DIST_NAME = "remedy-ai"
PYPI_URL = f"https://pypi.org/pypi/{PYPI_DIST_NAME}/json"
# Legacy dist names (editable / pre-rename installs) tried after remedy-ai.
_DIST_NAME_CANDIDATES = (PYPI_DIST_NAME, "remedy")


def _distribution_version() -> str | None:
    """Installed dist version from metadata (prefer remedy-ai)."""
    for name in _DIST_NAME_CANDIDATES:
        try:
            return importlib_version(name)
        except PackageNotFoundError:
            continue
        except Exception:
            continue
    return None


def _get_distribution():
    """Return importlib.metadata.Distribution for remedy-ai (or legacy name)."""
    from importlib.metadata import distribution

    for name in _DIST_NAME_CANDIDATES:
        try:
            dist = distribution(name)
            if dist is not None:
                return dist
        except Exception:
            continue
    return None


def _get_installed_version() -> str:
    """Returns the currently installed version string.

    Prefer ``remedy.__version__`` (source tree / frozen pyproject aware) so a
    stale site-packages dist-info (e.g. old ``remedy``/``remedy-ai`` wheel)
    cannot mask the checkout or sidecar version used for update decisions.
    """
    try:
        from remedy import __version__

        if __version__ and str(__version__) != "0.0.0":
            return str(__version__)
    except ImportError:
        pass
    ver = _distribution_version()
    if ver:
        return ver
    return "unknown"


def _get_latest_version() -> str | None:
    """Fetch latest PyPI version for remedy-ai. Returns None on failure."""
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            PYPI_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "Remedy-Updater",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data["info"]["version"]
    except Exception:
        return None


def _detect_install_source() -> str:
    """Determine how remedy was installed.

    Returns one of: 'git-editable', 'git-folder', 'pip', 'unknown'
    """
    try:
        dist = _get_distribution()
        if dist is None:
            return "unknown"

        # Check direct_url.json for editable install info
        direct_url = dist.read_text("direct_url.json")
        if direct_url:
            import json
            info = json.loads(direct_url)
            url = info.get("url", "")
            if "git" in url or url.endswith(".git"):
                if info.get("dir_info", {}).get("editable"):
                    return "git-editable"
                return "git-folder"
        return "pip"
    except Exception:
        return "unknown"


def _git_pull_and_reinstall(project_root: Path) -> bool:
    """Pull latest from git and reinstall the editable package."""
    import shutil

    git = shutil.which("git")
    if not git:
        console.print("[red]git not found in PATH[/red]")
        return False

    # git pull
    try:
        result = run_hidden(
            [git, "pull"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            console.print(f"[red]git pull failed:[/red]\n{result.stderr}")
            return False
        console.print(f"[dim]{result.stdout.strip()}[/dim]")
    except Exception as e:
        console.print(f"[red]git pull error: {e}[/red]")
        return False

    # pip install -e .
    try:
        result = run_hidden(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            console.print(f"[red]pip install failed:[/red]\n{result.stderr}")
            return False
    except Exception as e:
        console.print(f"[red]pip install error: {e}[/red]")
        return False

    return True


def _pip_upgrade() -> bool:
    """Upgrade remedy via pip."""
    try:
        result = run_hidden(
            [sys.executable, "-m", "pip", "install", "--upgrade", "remedy-ai"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            console.print(f"[red]pip upgrade failed:[/red]\n{result.stderr}")
            return False
        console.print(f"[dim]{result.stdout.strip()}[/dim]")
        return True
    except Exception as e:
        console.print(f"[red]pip upgrade error: {e}[/red]")
        return False


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple (major, minor, patch, …).

    Strips leading ``v``, ignores pre-release/build suffixes after ``-``/``+``.
    Non-numeric segments become 0 so ``0.10.26`` always compares cleanly.
    """
    try:
        s = str(v or "").strip().lstrip("vV")
        # Drop pre-release / build metadata for ordering of stable tags.
        for sep in ("-", "+"):
            if sep in s:
                s = s.split(sep, 1)[0]
        parts: list[int] = []
        for p in s.split("."):
            # Leading digits only. Joining *every* digit in the segment turned
            # a PEP 440 prerelease written without a separator into a bigger
            # number than the release it precedes: "1.2.3rc1" became (1, 2, 31),
            # so `remedy update` offered a release candidate as an upgrade from
            # the finished release. "3rc1" -> 3, "dev4" -> 0.
            num = ""
            for ch in p:
                if not ch.isdigit():
                    break
                num += ch
            parts.append(int(num) if num else 0)
        # Normalize to at least 3 components for stable ordering
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)
    except Exception:
        return (0, 0, 0)


def _is_newer(latest: str, installed: str) -> bool:
    """True if latest is strictly greater than installed."""
    return _parse_version(latest) > _parse_version(installed)


def _run_post_update_checks() -> None:
    """Verify core functionality after update."""
    console.print("\n[bold]Post-update verification:[/bold]")

    checks = [
        ("Version check", _version_check),
        ("Config valid", _config_check),
        ("Memory store", _memory_check),
    ]

    for label, func in checks:
        try:
            ok = func()
            status = "[green]OK[/green]" if ok else "[yellow]WARN[/yellow]"
            console.print(f"  {status}  {label}")
        except Exception as e:
            console.print(f"  [red]FAIL[/red] {label} - {e}")


def _version_check() -> bool:
    from remedy import __version__
    return bool(__version__)


def _config_check() -> bool:
    cfg = Path("~/.remedy/config.toml").expanduser()
    return cfg.exists()


def _memory_check() -> bool:
    return True  # db may not exist yet for fresh installs


def _is_remedy_pyproject(text: str) -> bool:
    """True if pyproject.toml is this product (remedy-ai), not unrelated 'remedy'."""
    # Prefer explicit dist name; also accept project.description / package layout markers.
    if 'name = "remedy-ai"' in text or "name = 'remedy-ai'" in text:
        return True
    # Pre-rename checkouts still used name = "remedy" with our description keywords.
    return bool(('name = "remedy"' in text or "name = 'remedy'" in text) and ("coding agent" in text.lower() or "remedy-ai" in text or "Ahmi" in text))


def _find_project_root() -> Path | None:
    """Try to locate the git-cloned project root for editable installs."""
    try:
        import remedy

        # package file is .../src/remedy/__init__.py (or site-packages/remedy)
        start = Path(remedy.__file__).resolve().parent
        for root in (start, *start.parents):
            if (root / ".git").exists() or (root / "pyproject.toml").exists():
                # Prefer a root that looks like the Remedy project
                if (root / "pyproject.toml").exists():
                    try:
                        text = (root / "pyproject.toml").read_text(encoding="utf-8")
                        if _is_remedy_pyproject(text):
                            return root
                    except OSError:
                        pass
                if (root / ".git").exists() and (root / "src" / "remedy").exists():
                    # Extra guard: must have our package layout
                    if (root / "src" / "remedy" / "interfaces" / "updater.py").is_file():
                        return root
    except Exception:
        pass
    return None


def run_update(check_only: bool = False) -> None:
    """Run the update check and optionally apply updates.

    Args:
        check_only: If True, only report available updates; don't install.
    """
    console.print(
        Panel.fit(
            "Checking for updates...",
            title="Remedy Updater",
            border_style="cyan",
        )
    )

    installed = _get_installed_version()
    console.print(f"  Installed: [bold]{installed}[/bold]")

    # PyPI check
    console.print("  Checking PyPI... ", end="")
    latest = _get_latest_version()
    if latest is None:
        console.print("[yellow]unreachable (offline?)[/yellow]")
    else:
        console.print(f"[bold cyan]{latest}[/bold cyan]")

    # Git check (for editable installs)
    install_src = _detect_install_source()
    project_root = _find_project_root()

    git_behind = False
    if install_src in ("git-editable", "git-folder") and project_root:
        console.print(f"  Install source: [dim]{install_src}[/dim]")
        console.print(f"  Project root:   [dim]{project_root}[/dim]")
        try:
            import shutil
            git = shutil.which("git")
            if git:
                run_hidden(
                    [git, "fetch", "origin"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Prefer origin/HEAD; fall back to master then main.
                behind = ""
                for ref in ("@{upstream}", "origin/master", "origin/main"):
                    r2 = run_hidden(
                        [git, "rev-list", "--count", f"HEAD..{ref}", "--"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if r2.returncode == 0 and (r2.stdout or "").strip().isdigit():
                        behind = (r2.stdout or "").strip()
                        break
                if behind and behind != "0":
                    git_behind = True
                    console.print(f"  Git behind by: [yellow]{behind} commits[/yellow]")
                else:
                    console.print("  Git status:    [green]up to date[/green]")
        except Exception:
            pass

    updatable = False
    if latest and git_behind:
        updatable = True
    elif latest and _is_newer(latest, installed):
        updatable = True
        console.print(f"\n[bold yellow]Update available: {installed} -> {latest}[/bold yellow]")
    elif latest and latest != installed:
        console.print(f"\n[dim]PyPI has {latest} (installed: {installed} is newer or different)[/dim]")
    elif git_behind:
        updatable = True
        console.print(f"\n[bold yellow]Git update available ({installed})[/bold yellow]")
    elif latest is None:
        console.print("\n[red]Could not reach PyPI; update status unknown (offline?).[/red]")
        raise SystemExit(1)
    else:
        console.print("\n[green]Remedy is up to date.[/green]")

    if not updatable:
        return

    if check_only:
        console.print("\n[dim]Use 'remedy update' (without --check) to apply updates.[/dim]")
        return

    console.print()
    if not (sys.stdin is not None and sys.stdin.isatty()):
        console.print("[yellow]Non-interactive terminal; use --check first, then re-run to apply.[/yellow]")
        return
    if not Confirm.ask("Apply update now?"):
        console.print("[yellow]Update cancelled.[/yellow]")
        return

    success = False
    if install_src in ("git-editable", "git-folder") and project_root:
        # Official origin replaces local self-improve. Never merge LLM patches.
        with contextlib.suppress(Exception):
            from remedy.core.self_inject_draft import origin_wins_if_dirty

            policy = origin_wins_if_dirty(project_root)
            if policy.get("action") == "abort_dirty":
                console.print(
                    "\n[red]Local self-improve or WIP is dirty.[/red] "
                    "Official update does not merge per-client patches."
                )
                console.print(
                    "[dim]Post the draft to the GitHub inbox issue "
                    "(`self_improve_submit_issue`) or discard it "
                    "(`git reset --hard` + clean) then retry.[/dim]"
                )
                raise SystemExit(1)
        console.print("\n[bold]Pulling from git + reinstalling...[/bold]")
        success = _git_pull_and_reinstall(project_root)
    else:
        console.print("\n[bold]Upgrading via pip...[/bold]")
        success = _pip_upgrade()

    if success:
        console.print("\n[bold green]Update complete![/bold green]")
        new_version = _get_installed_version()
        console.print(f"  New version: [bold]{new_version}[/bold]")
        _run_post_update_checks()
    else:
        console.print("\n[red]Update failed. Try manually:[/red]")
        console.print(
            "  git clone https://github.com/AhmiDarrow/RemedyAI.git && cd RemedyAI"
        )
        console.print("  pip install -e .")
        console.print("  # or: pip install --upgrade remedy-ai")
        raise SystemExit(1)

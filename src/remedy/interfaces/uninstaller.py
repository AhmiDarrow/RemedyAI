"""Uninstaller for Remedy.

Removes the package and optionally purges all user data.

Usage:
    remedy uninstall            # remove package, keep ~/.remedy/
    remedy uninstall --purge    # remove package + all user data
    remedy uninstall --dry-run  # show what would be removed
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()

REMEDY_HOME = Path("~/.remedy").expanduser()


def _assert_safe_wipe_root(home: Path | None = None) -> Path:
    """Refuse wipe unless path is a ``.remedy`` dir with a non-root parent.

    Guards against a mis-set ``REMEDY_HOME`` that would ``rmtree`` the user
    profile, a drive root, or an arbitrary non-product tree. Tests may point
    ``REMEDY_HOME`` at ``tmp_path/.remedy`` — still named ``.remedy`` and not
    at a filesystem root, so allowed.
    """
    target = Path(home or REMEDY_HOME).expanduser()
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target.absolute()
    # Must be named .remedy — never wipe arbitrary trees
    if resolved.name.lower() != ".remedy":
        raise RuntimeError(
            f"Refuse wipe: path must be a .remedy directory, got {resolved}"
        )
    parent = resolved.parent
    # Never wipe drive/filesystem root or the path itself
    if resolved.parent == resolved or parent == parent.parent:
        raise RuntimeError(f"Refuse wipe of .remedy at drive/FS root: {resolved}")
    try:
        user_home = Path.home().expanduser().resolve()
    except OSError:
        user_home = Path.home().expanduser().absolute()
    if resolved == user_home:
        raise RuntimeError(f"Refuse wipe of user home: {resolved}")
    # Refuse known high-value OS dirs even if somehow named .remedy (paranoia)
    blocked_names = {
        "windows",
        "system32",
        "program files",
        "program files (x86)",
        "programdata",
    }
    if any(part.lower() in blocked_names for part in resolved.parts[:-1]):
        raise RuntimeError(f"Refuse wipe under system path: {resolved}")
    return resolved


def _get_package_path() -> Path | None:
    """Locate the installed remedy package directory."""
    try:
        import remedy
        return Path(remedy.__file__).resolve().parent
    except Exception:
        return None


def _get_data_files() -> list[tuple[str, Path]]:
    """List all Remedy user data files with descriptions."""
    entries: list[tuple[str, Path]] = []

    if REMEDY_HOME.exists():
        entries.append(("config dir", REMEDY_HOME))
        for f in REMEDY_HOME.rglob("*"):
            if f.is_file():
                rel = f.relative_to(REMEDY_HOME)
                entries.append((f"data file - {rel}", f))

    # Also check for pip cache/build artifacts
    return entries


def _pip_uninstall() -> bool:
    """Run pip uninstall for both distribution names (remedy-ai and legacy remedy)."""
    ok = True
    # remedy-ai is the real PyPI name; also try "remedy" for editable/legacy installs
    for dist in ("remedy-ai", "remedy"):
        try:
            from remedy.execution.process import run_hidden

            result = run_hidden(
                [sys.executable, "-m", "pip", "uninstall", "-y", dist],
                capture_output=True,
                text=True,
                timeout=60,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if out:
                console.print(f"[dim]{out}[/dim]")
            if result.returncode != 0 and "not installed" not in err.lower() and "Skipping" not in err:
                console.print(f"[yellow]pip uninstall warning ({dist}):[/yellow]\n{err}")
                ok = False
        except Exception as e:
            console.print(f"[red]pip uninstall failed ({dist}): {e}[/red]")
            ok = False
    return ok


def _wipe_config() -> None:
    """Remove config/auth/desktop prefs; leave skills and memory."""
    home = _assert_safe_wipe_root()
    for name in ("config.toml", "config.yaml", "config.yml", "desktop.json", "comfyui.json"):
        p = home / name
        if p.exists():
            p.unlink(missing_ok=True)
            console.print(f"  removed [dim]{p}[/dim]")
    auth = home / "auth"
    if auth.exists():
        shutil.rmtree(auth, ignore_errors=True)
        console.print(f"  removed [dim]{auth}[/dim]")
    # vision.json removed with full vision tree via _wipe_vision() (called after)


def _stop_llama_server_processes() -> None:
    """Best-effort kill so vision tree is not file-locked on Windows."""
    import subprocess

    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/IM", "llama-server.exe", "/T"],
                capture_output=True,
                timeout=15,
                creationflags=flags,
                check=False,
            )
    else:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["pkill", "-f", "llama-server"],
                capture_output=True,
                timeout=10,
                check=False,
            )


def _wipe_vision() -> None:
    """Remove local visual decoder runtime + models under ~/.remedy/vision.

    Always stops llama-server first, then deletes runtime binaries, GGUF models,
    downloads, and vision.json so reinstall starts clean.
    """
    home = _assert_safe_wipe_root()
    _stop_llama_server_processes()
    vision = home / "vision"
    try:
        from remedy.vision.install import wipe_vision_data

        result = wipe_vision_data(home)
        if vision.exists():
            shutil.rmtree(vision, ignore_errors=True)
        console.print(
            f"  removed [dim]{vision}[/dim] (llama.cpp + visual models)"
            + (f" — {result}" if result else "")
        )
    except Exception:
        if vision.exists():
            shutil.rmtree(vision, ignore_errors=True)
            console.print(f"  removed [dim]{vision}[/dim]")
    # Legacy stray paths
    for stray in ("llama-server", "llama.cpp"):
        p = home / stray
        if p.exists():
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def _wipe_skills() -> None:
    home = _assert_safe_wipe_root()
    skills = home / "skills"
    if skills.exists():
        shutil.rmtree(skills, ignore_errors=True)
        console.print(f"  removed [dim]{skills}[/dim]")
    # Durable skill execution stats live next to the skills tree
    stats = home / "skill_stats.json"
    if stats.exists():
        stats.unlink(missing_ok=True)
        console.print(f"  removed [dim]{stats}[/dim]")


def run_uninstall(
    purge: bool = False,
    dry_run: bool = False,
    *,
    config: bool = False,
    skills: bool = False,
) -> None:
    """Run the uninstaller.

    Args:
        purge: Full wipe of ~/.remedy (and implies config + skills).
        dry_run: Show what would be removed without touching anything.
        config: Remove configuration / auth only.
        skills: Remove ~/.remedy/skills only.
    """
    if purge:
        config = True
        skills = True

    console.print(
        Panel.fit(
            "Preparing to uninstall Remedy...",
            title="Uninstaller",
            border_style="yellow",
        )
    )

    pkg_path = _get_package_path()
    data_files = _get_data_files()

    # Show what would be affected
    if pkg_path:
        console.print(f"\n[bold]Package:[/bold]  [dim]{pkg_path}[/dim]")
    else:
        console.print("\n[bold]Package:[/bold]  [dim]not found (may already be removed)[/dim]")

    for desc, path in data_files:
        if path.is_file():
            console.print(f"  {desc}: [dim]{path}[/dim]")

    keep = not (purge or config or skills)
    console.print(
        f"\n[bold]Home dir:[/bold] [dim]{REMEDY_HOME}[/dim] "
        + (
            "[yellow](kept)[/yellow]"
            if keep
            else (
                "[red](full wipe)[/red]"
                if purge
                else f"[yellow](partial: config={config} skills={skills})[/yellow]"
            )
        )
    )

    if dry_run:
        console.print("\n[bold cyan]Dry run complete. No changes made.[/bold cyan]")
        console.print("Run without [bold]--dry-run[/bold] to proceed.")
        return

    # Confirm
    console.print()
    if purge:
        action = "Uninstall package AND full-wipe all Remedy data?"
        suffix = "\n[red]This cannot be undone![/red]"
    elif config or skills:
        bits = []
        if config:
            bits.append("config")
        if skills:
            bits.append("skills")
        action = f"Uninstall package and remove {', '.join(bits)}?"
        suffix = ""
    else:
        action = "Uninstall the remedy package (keep user data)?"
        suffix = ""

    if not Confirm.ask(f"{action}{suffix}", default=False, console=console):
        console.print("[yellow]Uninstall cancelled.[/yellow]")
        return

    # Uninstall package
    console.print("\n[bold]Uninstalling package...[/bold]")
    _pip_uninstall()

    # Data wipe (path-safety guard before any rmtree)
    if purge and REMEDY_HOME.exists():
        try:
            wipe_root = _assert_safe_wipe_root()
        except RuntimeError as e:
            console.print(f"[red]Wipe aborted: {e}[/red]")
            return
        console.print(f"\n[bold]Full wipe {wipe_root}...[/bold]")
        # Stop vision server before deleting weights/runtime
        with contextlib.suppress(Exception):
            _wipe_vision()
        try:
            shutil.rmtree(wipe_root)
            console.print("[green]Remedy data removed.[/green]")
        except Exception as e:
            console.print(f"[red]Failed to remove data: {e}[/red]")
    else:
        if config or skills:
            console.print("\n[bold]Removing selected user data...[/bold]")
        if config:
            _wipe_config()
            # Config wipe also drops vision runtime/models (large; re-download on reinstall)
            _wipe_vision()
        if skills:
            _wipe_skills()

    console.print()
    console.print("[green]Uninstall complete.[/green]")
    console.print("\nThanks for trying Remedy!")
    console.print(
        "To reinstall: [dim]pip install remedy-ai[/dim] "
        "or [dim]pip install -e .[/dim] from the repo"
    )

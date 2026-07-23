"""Uninstaller for Remedy.

Removes the package and optionally purges all user data.

Usage:
    remedy uninstall            # remove package, keep ~/.remedy/
    remedy uninstall --purge    # remove package + all user data
    remedy uninstall --dry-run  # show what would be removed
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()

REMEDY_HOME = Path("~/.remedy").expanduser()


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


def run_uninstall(purge: bool = False, dry_run: bool = False) -> None:
    """Run the uninstaller.

    Args:
        purge: Also remove ~/.remedy/ user data directory.
        dry_run: Show what would be removed without touching anything.
    """
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

    console.print(f"\n[bold]Home dir:[/bold] [dim]{REMEDY_HOME}[/dim] {'[yellow](will be kept)[/yellow]' if not purge else '[red](will be removed)[/red]'}")

    if dry_run:
        console.print("\n[bold cyan]Dry run complete. No changes made.[/bold cyan]")
        console.print("Run without [bold]--dry-run[/bold] to proceed.")
        return

    # Confirm
    console.print()
    if purge:
        action = "Uninstall package AND delete all Remedy data?"
        suffix = "\n[red]This cannot be undone![/red]"
    else:
        action = "Uninstall the remedy package?"
        suffix = ""

    if not Confirm.ask(f"{action}{suffix}", default=False, console=console):
        console.print("[yellow]Uninstall cancelled.[/yellow]")
        return

    # Uninstall package
    console.print("\n[bold]Uninstalling package...[/bold]")
    _pip_uninstall()

    # Purge data
    if purge and REMEDY_HOME.exists():
        console.print(f"\n[bold]Removing {REMEDY_HOME}...[/bold]")
        try:
            shutil.rmtree(REMEDY_HOME)
            console.print("[green]Remedy data removed.[/green]")
        except Exception as e:
            console.print(f"[red]Failed to remove data: {e}[/red]")

    console.print()
    console.print("[green]Uninstall complete.[/green]")
    console.print("\nThanks for trying Remedy!")
    console.print(
        "To reinstall: [dim]pip install remedy-ai[/dim] "
        "or [dim]pip install -e .[/dim] from the repo"
    )

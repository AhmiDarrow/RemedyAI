"""Tailscale opt-in bootstrap for RemedyConnect.

"Once opted in, Remedy should autodownload anything needed and advise users
how to pair." This module owns the PC side of that flow:

- detect an existing Tailscale install (CLI on PATH or the standard
  ``C:\\Program Files\\Tailscale\\tailscale.exe`` location)
- report status (installed / running / logged in / tailnet IPv4)
- when the user opts in and Tailscale is missing, download the official
  Windows MSI from ``pkgs.tailscale.com`` (version resolved from the
  stable JSON feed) and launch the installer
- start the login flow (``tailscale up`` prints a URL the app can open)

It never stores auth keys, never hardcodes a tailnet address, and never
touches the user's Tailscale credentials. The only machine-specific value it
exposes is the *tailnet IPv4* (100.64.0.0/10) needed for pairing — the same
value the QR already advertises.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PKGS_FEED = "https://pkgs.tailscale.com/stable/?mode=json"
PKGS_BASE = "https://pkgs.tailscale.com/stable/"

_TAILSCALE_MSI = "tailscale-setup-{version}-{arch}.msi"
_KNOWN_INSTALL_DIRS = (
    r"C:\Program Files\Tailscale",
    r"C:\Program Files (x86)\Tailscale",
)


def tailscale_cli() -> str:
    """Path to the Tailscale CLI, or ``""`` when not installed."""
    exe = "tailscale.exe" if sys.platform == "win32" else "tailscale"
    found = shutil.which(exe)
    if found:
        return found
    for base in _KNOWN_INSTALL_DIRS:
        cand = Path(base) / exe
        if cand.is_file():
            return str(cand)
    return ""


def tailscale_installed() -> bool:
    return bool(tailscale_cli())


def _run_cli(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    """Run ``tailscale <args>``; returns (exit_code, combined output)."""
    cli = tailscale_cli()
    if not cli:
        return 127, "tailscale not installed"
    try:
        proc = subprocess.run(
            [cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except FileNotFoundError:
        return 127, "tailscale not found"
    except subprocess.TimeoutExpired as exc:
        # ``tailscale up`` on an unauthenticated node prints the sign-in URL
        # and then blocks until the browser completes auth. Keep what it
        # printed so the caller can hand that URL to the user.
        parts = []
        for chunk in (exc.stdout, exc.stderr):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            if chunk:
                parts.append(str(chunk))
        out = "".join(parts).strip()
        return 124, out or "tailscale timed out"
    except OSError as exc:
        return 126, str(exc)


def tailscale_tailnet_ipv4() -> str:
    """Tailnet IPv4 from ``tailscale ip -4`` (authoritative), else ``""``.

    The interface-scan fallback in :func:`remedy.connect.bind.tailscale_ipv4`
    stays for the QR path; this prefers the CLI so an opted-in install that is
    not yet visible to the address scan still reports correctly.
    """
    rc, out = _run_cli("ip", "-4")
    if rc != 0:
        return ""
    for line in (out or "").splitlines():
        cand = line.strip()
        if cand.startswith("100."):
            return cand
    return ""


def tailscale_status() -> dict:
    """Snapshot of the PC's Tailscale state. Never raises.

    Returns::

        {
          "installed": bool,
          "running": bool,      # service/daemon reachable
          "logged_in": bool,    # tailnet IPv4 assigned
          "tailnet_ipv4": str,  # "" when not connected
          "version": str,       # "" when unknown
          "error": str,         # human hint when not fully ready
        }
    """
    installed = tailscale_installed()
    if not installed:
        return {
            "installed": False,
            "running": False,
            "logged_in": False,
            "tailnet_ipv4": "",
            "version": "",
            "error": "Tailscale is not installed. Opt in to download it automatically.",
        }
    rc, sout = _run_cli("status", timeout=6.0)
    # ``tailscale status`` exits non-zero while the daemon is up but logged
    # out; that is "running, needs sign-in", not "not running".
    running = rc == 0 or "logged out" in (sout or "").lower()
    ts_ip = tailscale_tailnet_ipv4() if running else ""
    version = ""
    if running:
        vrc, vout = _run_cli("version", timeout=6.0)
        if vrc == 0:
            version = (vout or "").splitlines()[0].strip() if vout else ""
    logged_in = bool(ts_ip)
    error = ""
    if not running:
        error = "Tailscale is installed but not running. Start it, then sign in."
    elif not logged_in:
        error = "Tailscale is running but not logged in. Sign in to get a tailnet address."
    return {
        "installed": installed,
        "running": running,
        "logged_in": logged_in,
        "tailnet_ipv4": ts_ip,
        "version": version,
        "error": error,
    }


def latest_windows_msi_url() -> str:
    """Official URL of the newest stable Windows x64 Tailscale MSI.

    The ``latest`` alias is not published; the stable JSON feed names the
    current version. ``?mode=json`` returns ``MSIs.amd64``.
    """
    try:
        with urllib.request.urlopen(PKGS_FEED, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"could not fetch Tailscale package feed: {exc}") from exc
    ms = data.get("MSIs") or {}
    name = str(ms.get("amd64") or "").strip()
    if not name:
        raise RuntimeError("Tailscale package feed has no amd64 MSI")
    return PKGS_BASE + name


def ensure_tailscale(download_dir: str | Path | None = None) -> dict:
    """Download + launch the official Tailscale installer when missing.

    Returns::

        {
          "status": "installed" | "downloading" | "already_installed" | "error",
          "message": str,
          "msi_path": str,          # local file, when downloaded
          "installer_url": str,     # official URL, informational
        }

    Idempotent: when the CLI is already present it returns
    ``already_installed`` without touching anything. When missing it downloads
    the official MSI to the given directory (default: the OS temp dir) and
    launches ``msiexec /i`` (which triggers the normal UAC elevation prompt —
    the user just clicks Yes).
    """
    if tailscale_installed():
        return {
            "status": "already_installed",
            "message": "Tailscale is already installed.",
            "msi_path": "",
            "installer_url": "",
        }
    if sys.platform != "win32":
        # The auto-installer is the Windows MSI; do not download it elsewhere.
        return {
            "status": "error",
            "message": "Automatic install is Windows-only here. Install Tailscale from tailscale.com/download.",
            "msi_path": "",
            "installer_url": "",
        }
    try:
        url = latest_windows_msi_url()
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc), "msi_path": "", "installer_url": ""}
    name = url.rsplit("/", 1)[-1]
    target = Path(download_dir) if download_dir else Path(tempdir_root()) / "remedy-tailscale"
    target.mkdir(parents=True, exist_ok=True)
    dest = target / name
    try:
        if not dest.is_file() or dest.stat().st_size == 0:
            _download(url, dest)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"download failed: {exc}",
            "msi_path": str(dest),
            "installer_url": url,
        }
    if sys.platform == "win32":
        try:
            subprocess.Popen(["msiexec", "/i", str(dest)])  # UAC prompt → user clicks Yes
        except OSError as exc:
            return {
                "status": "error",
                "message": f"could not launch installer: {exc}",
                "msi_path": str(dest),
                "installer_url": url,
            }
        return {
            "status": "downloading",
            "message": (
                f"Downloaded {name}. Follow the installer, then sign in when it "
                "opens — use the same account as your phone."
            ),
            "msi_path": str(dest),
            "installer_url": url,
        }
    return {
        "status": "downloading",
        "message": f"Downloaded {name} to {dest}. Run it to install Tailscale.",
        "msi_path": str(dest),
        "installer_url": url,
    }


def start_tailscale_login() -> dict:
    """Kick off Tailscale sign-in and return the login URL when available.

    ``tailscale up`` on an unauthenticated node prints
    ``To authenticate, visit: <url>``. We capture that and hand it back so
    Settings can open it. Returns ``{"status": ..., "message": ..., "login_url": ...}``.
    """
    if not tailscale_installed():
        return {
            "status": "error",
            "message": "Tailscale is not installed yet.",
            "login_url": "",
        }
    rc, out = _run_cli("up", timeout=10.0)
    # Never return the bare "https://login.tailscale.com/" prefix: only a
    # full URL (with its node token) is worth opening.
    match = re.search(r"https://login\.tailscale\.com/\S+", out or "")
    url = match.group(0).rstrip(".,;)") if match else ""
    if rc == 0:
        return {
            "status": "ok",
            "message": "Tailscale is already signed in.",
            "login_url": "",
        }
    if url:
        return {
            "status": "needs_login",
            "message": "Open the sign-in link to connect this PC to your tailnet.",
            "login_url": url,
        }
    return {
        "status": "needs_login",
        "message": "Start the Tailscale app and sign in — use the same account as your phone.",
        "login_url": "",
    }


def pair_guidance(status: dict) -> str:
    """One-paragraph guidance for the Settings UI given a status dict."""
    if not status.get("installed"):
        return (
            "Install the free Tailscale app on this PC and on your phone, then sign "
            "into the same account on both. Remedy can download and launch the PC "
            "installer for you — tap Install. Once connected, scan the pairing QR "
            "and RemedyConnect works on Wi-Fi and mobile data."
        )
    if not status.get("logged_in"):
        return (
            "Tailscale is installed. Sign in on this PC (and on your phone, same "
            "account), then scan the pairing QR. Mobile data will work everywhere."
        )
    return (
        "Tailscale is connected. Install the Tailscale app on your phone and sign "
        "into the same account, then scan the pairing QR — it carries the tailnet "
        "address, so RemedyConnect works on Wi-Fi and mobile data."
    )


def tempdir_root() -> str:
    """Windows temp root (respects TMP/TEMP), else system temp."""
    return os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=1024 * 256)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

"""Tailscale opt-in bootstrap: detect, status, MSI autodownload, login.

Pins the seams that make "once opted in, Remedy autodownloads what it needs"
work without touching the user's Tailscale credentials: CLI discovery,
status shaping, the official MSI URL from the stable JSON feed, the
idempotent install launcher, and the sign-in URL capture.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.request

from remedy.connect import tailscale_bootstrap as tsb


def test_tailscale_cli_finds_path(monkeypatch) -> None:
    monkeypatch.setattr(tsb.shutil, "which", lambda _exe: r"C:\Program Files\Tailscale\tailscale.exe")
    assert tsb.tailscale_cli() == r"C:\Program Files\Tailscale\tailscale.exe"


def test_tailscale_cli_known_install_dir(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "tailscale.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(tsb.shutil, "which", lambda _exe: "")
    monkeypatch.setattr(tsb, "_KNOWN_INSTALL_DIRS", (str(tmp_path),))
    assert tsb.tailscale_cli() == str(exe)


def test_tailscale_cli_missing(monkeypatch) -> None:
    monkeypatch.setattr(tsb.shutil, "which", lambda _exe: "")
    monkeypatch.setattr(tsb, "_KNOWN_INSTALL_DIRS", ())
    assert tsb.tailscale_cli() == ""


def test_status_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: False)
    st = tsb.tailscale_status()
    assert st["installed"] is False
    assert st["logged_in"] is False
    assert "download" in st["error"].lower()


def test_status_installed_not_running(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: True)
    monkeypatch.setattr(tsb, "_run_cli", lambda *a, **k: (1, "failed to connect to local Tailscale service"))
    st = tsb.tailscale_status()
    assert st["installed"] is True
    assert st["running"] is False
    assert st["logged_in"] is False
    assert "not running" in st["error"]


def test_status_running_not_logged_in(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: True)

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", "")
        if cmd == "status":
            return 0, "Logged out."
        if cmd == "ip":
            return 0, ""
        if cmd == "version":
            return 0, "1.102.3"
        return 0, ""

    monkeypatch.setattr(tsb, "_run_cli", fake_run)
    st = tsb.tailscale_status()
    assert st["running"] is True
    assert st["logged_in"] is False
    assert "not logged in" in st["error"]


def test_status_logged_in_has_tailnet_ip(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: True)

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", "")
        if cmd == "status":
            return 0, "Logged in"
        if cmd == "ip":
            return 0, "100.101.102.103"
        if cmd == "version":
            return 0, "1.102.3"
        return 0, ""

    monkeypatch.setattr(tsb, "_run_cli", fake_run)
    st = tsb.tailscale_status()
    assert st["running"] is True
    assert st["logged_in"] is True
    assert st["tailnet_ipv4"] == "100.101.102.103"
    assert st["version"] == "1.102.3"
    assert st["error"] == ""


def test_latest_windows_msi_url_parses_feed(monkeypatch) -> None:
    payload = json.dumps({"MSIs": {"amd64": "tailscale-setup-1.102.3-amd64.msi"}}).encode()
    fake = io.BytesIO(payload)

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a, **k):
            return fake.read()

    monkeypatch.setattr(urllib.request, "urlopen", lambda _url, timeout=15: FakeResp())
    url = tsb.latest_windows_msi_url()
    assert url == "https://pkgs.tailscale.com/stable/tailscale-setup-1.102.3-amd64.msi"


def test_latest_windows_msi_url_missing_entry(monkeypatch) -> None:
    fake = io.BytesIO(json.dumps({"MSIs": {}}).encode())

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a, **k):
            return fake.read()

    monkeypatch.setattr(urllib.request, "urlopen", lambda _url, timeout=15: FakeResp())
    try:
        tsb.latest_windows_msi_url()
    except RuntimeError as exc:
        assert "no amd64 MSI" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_ensure_tailscale_already_installed(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: True)
    r = tsb.ensure_tailscale()
    assert r["status"] == "already_installed"
    assert r["msi_path"] == ""


def test_ensure_tailscale_downloads_and_launches(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: False)
    monkeypatch.setattr(
        tsb,
        "latest_windows_msi_url",
        lambda: "https://pkgs.tailscale.com/stable/tailscale-setup-9.9.9-amd64.msi",
    )
    monkeypatch.setattr(
        tsb,
        "_download",
        lambda url, dest: dest.write_bytes(b"msi"),
    )
    launched: list[list[str]] = []

    def fake_popen(args, **kw):
        launched.append(list(args))
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tsb.sys, "platform", "win32")
    r = tsb.ensure_tailscale(download_dir=str(tmp_path))
    assert r["status"] == "downloading"
    assert r["installer_url"].endswith("tailscale-setup-9.9.9-amd64.msi")
    assert launched and launched[0][0] == "msiexec"
    assert launched[0][1] == "/i"
    assert (tmp_path / "tailscale-setup-9.9.9-amd64.msi").is_file()


def test_ensure_tailscale_download_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: False)
    monkeypatch.setattr(
        tsb,
        "latest_windows_msi_url",
        lambda: "https://pkgs.tailscale.com/stable/tailscale-setup-9.9.9-amd64.msi",
    )

    def boom(url, dest):
        raise OSError("network down")

    monkeypatch.setattr(tsb, "_download", boom)
    r = tsb.ensure_tailscale(download_dir=str(tmp_path))
    assert r["status"] == "error"
    assert "network down" in r["message"]


def test_start_login_returns_url(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: True)
    monkeypatch.setattr(
        tsb,
        "_run_cli",
        lambda *a, **k: (
            1,
            "To authenticate, visit:\n\thttps://login.tailscale.com/a/abc123\n",
        ),
    )
    r = tsb.start_tailscale_login()
    assert r["status"] == "needs_login"
    assert r["login_url"].startswith("https://login.tailscale.com/")


def test_start_login_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(tsb, "tailscale_installed", lambda: False)
    r = tsb.start_tailscale_login()
    assert r["status"] == "error"


def test_pair_guidance_branches() -> None:
    missing = tsb.pair_guidance({"installed": False})
    assert "Install" in missing
    installed = tsb.pair_guidance({"installed": True, "logged_in": False})
    assert "Sign in" in installed
    ready = tsb.pair_guidance({"installed": True, "logged_in": True})
    assert "tailnet" in ready

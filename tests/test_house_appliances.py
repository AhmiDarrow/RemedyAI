"""Her house: appliance inventory, natural-name resolution, household agency."""

from __future__ import annotations

from pathlib import Path

from remedy.core.computer.appliances import (
    ApplianceInventory,
    appliance_overview,
    best_appliance,
    ensure_appliances,
    load_inventory,
    needs_scan,
    resolve_appliance,
    scan_appliances,
    score_match,
    suggestions_line,
)
from remedy.core.computer.household import (
    available_manager,
    house_walkthrough,
    plan_addition,
)


def _fake_start_menu(root: Path) -> Path:
    programs = root / "Programs"
    (programs / "Music").mkdir(parents=True)
    (programs / "Spotify.lnk").write_bytes(b"")
    (programs / "Music" / "VLC media player.lnk").write_bytes(b"")
    (programs / "Word 2024.lnk").write_bytes(b"")
    (programs / "Steam.lnk").write_bytes(b"")
    (programs / "Uninstall Spotify.lnk").write_bytes(b"")  # junk
    (programs / "readme.lnk").write_bytes(b"")  # junk
    (programs / "notes.txt").write_bytes(b"")  # not an appliance
    return programs


# --- scanning --------------------------------------------------------------


def test_scan_finds_appliances_and_skips_junk(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    inv = scan_appliances(tmp_path / "home", roots=[programs])
    names = {a.name for a in inv.appliances}
    assert names == {"Spotify", "VLC media player", "Word 2024", "Steam"}
    vlc = next(a for a in inv.appliances if a.name.startswith("VLC"))
    assert vlc.room == "Music"


def test_scan_persists_and_staleness(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    home = tmp_path / "home"
    assert needs_scan(home) is True
    scan_appliances(home, roots=[programs])
    assert needs_scan(home) is False
    inv = load_inventory(home)
    assert inv is not None and len(inv.appliances) == 4


def test_desktop_entries_parse_name(tmp_path):
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "gimp.desktop").write_text(
        "[Desktop Entry]\nName=GIMP Image Editor\nExec=gimp\n", encoding="utf-8"
    )
    (apps / "hidden.desktop").write_text(
        "[Desktop Entry]\nName=Ghost\nNoDisplay=true\n", encoding="utf-8"
    )
    inv = scan_appliances(tmp_path / "home", roots=[apps])
    names = {a.name for a in inv.appliances}
    assert names == {"GIMP Image Editor"}


def test_ensure_appliances_foreground(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    # Force a scan path that finds nothing (default roots absent in sandbox)
    inv = ensure_appliances(tmp_path / "home", background=False)
    assert inv is not None  # empty is fine; scan itself must not fail
    inv2 = scan_appliances(tmp_path / "home", roots=[programs])
    assert len(inv2.appliances) == 4


# --- resolution ------------------------------------------------------------


def test_natural_names_resolve(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    home = tmp_path / "home"
    scan_appliances(home, roots=[programs])
    assert best_appliance("spotify", home).name == "Spotify"
    assert best_appliance("word", home).name == "Word 2024"
    assert best_appliance("vlc", home).name == "VLC media player"
    assert best_appliance("definitely-not-here", home) is None


def test_scoring_orders_exact_over_fuzzy():
    assert score_match("spotify", "Spotify") == 100
    assert score_match("spot", "Spotify") > score_match("fy", "Spotify")
    assert score_match("word", "Word 2024") > 60
    assert score_match("xyz", "Spotify") == 0


def test_suggestions_for_near_miss(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    home = tmp_path / "home"
    scan_appliances(home, roots=[programs])
    line = suggestions_line("spotfy", home)
    assert "Spotify" in line
    matches = resolve_appliance("media", home)
    assert matches and matches[0].appliance.name == "VLC media player"


def test_overview_lists_and_searches(tmp_path):
    programs = _fake_start_menu(tmp_path / "sm")
    home = tmp_path / "home"
    scan_appliances(home, roots=[programs])
    all_view = appliance_overview("", home)
    assert all_view["total_known"] == 4
    hit = appliance_overview("steam", home)
    assert hit["appliances"][0]["name"] == "Steam"


def test_inventory_roundtrip_bounds():
    inv = ApplianceInventory.from_dict(
        {"appliances": [{"name": "A" * 500, "path": "p"}] * 5}
    )
    assert all(len(a.name) <= 120 for a in inv.appliances)


# --- household: secure + additions -----------------------------------------


def test_walkthrough_is_readonly_report(tmp_path):
    rep = house_walkthrough(tmp_path)
    assert rep["ok"] is True
    doors = {d["door"] for d in rep["doors"]}
    assert {"remedy", "ollama"} <= doors
    assert all(isinstance(d["open"], bool) for d in rep["doors"])
    assert "read-only" in rep["note"]


def test_addition_plans_argv_never_runs(tmp_path):
    which = lambda name: "/usr/bin/apt-get" if name == "apt" else None  # noqa: E731
    plan = plan_addition("ffmpeg", which=which)
    assert plan["ok"] is True
    assert plan["manager"] == "apt"
    assert "ffmpeg" in plan["argv"]
    assert "PLAN" in plan["note"]


def test_addition_jails_package_names():
    which = lambda name: "/x/winget" if name == "winget" else None  # noqa: E731
    assert plan_addition("ffmpeg; rm -rf /", which=which)["ok"] is False
    assert plan_addition("https://evil.example/x.exe", which=which)["ok"] is False
    assert plan_addition("../../escape", which=which)["ok"] is False
    good = plan_addition("Microsoft.VisualStudioCode", which=which)
    assert good["ok"] is True and good["manager"] == "winget"


def test_addition_without_manager_is_honest():
    plan = plan_addition("ffmpeg", which=lambda name: None)
    assert plan["ok"] is False
    assert "package manager" in plan["error"]


def test_manager_preference_order():
    def which_all(name):
        return f"/x/{name}"

    import os

    expected = "winget" if os.name == "nt" else "brew"
    assert available_manager(which=which_all) == expected

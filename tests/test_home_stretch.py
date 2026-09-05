"""First-home stretch — census of hardware, tools, rooms, doors via host_binding."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remedy.core.computer import host_binding


def test_stretch_home_persists(tmp_path: Path) -> None:
    home = str(tmp_path)
    census = host_binding.stretch_home(home, force=True)
    assert census.get("stretched_at")
    assert census.get("os_name")
    assert int(census.get("cpu_count") or 0) >= 1
    tools = census.get("tools") or {}
    assert isinstance(tools, dict) and "python" in tools
    path = tmp_path / "host" / "home.json"
    assert path.is_file()
    loaded = host_binding.stretch_load(home)
    assert loaded is not None
    assert loaded.get("os_name") == census.get("os_name")
    loaded_tools = loaded.get("tools") or {}
    assert loaded_tools.get("python") == tools.get("python")


def test_stretch_skips_when_fresh(tmp_path: Path) -> None:
    home = str(tmp_path)
    first = host_binding.stretch_home(home, force=True)
    again = host_binding.stretch_home(home, force=False)
    assert again.get("stretched_at") == first.get("stretched_at")


def test_needs_stretch_stale(tmp_path: Path) -> None:
    home = str(tmp_path)
    host_binding.stretch_home(home, force=True)
    assert host_binding.stretch_needs(home) is False
    path = tmp_path / "host" / "home.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(UTC) - timedelta(days=20)
    raw["stretched_at"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert host_binding.stretch_needs(home) is True


def test_needs_stretch_missing(tmp_path: Path) -> None:
    assert host_binding.stretch_needs(str(tmp_path)) is True


def test_census_strips_secret_shaped_keys(tmp_path: Path) -> None:
    path = tmp_path / "host" / "home.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "stretched_at": "2026-01-01T00:00:00Z",
                "tools": {
                    "python": "/usr/bin/python",
                    "openai_api_key": "sk-leak",
                },
                "rooms": {"desktop": "C:/ok", "auth_token": "nope"},
                "doors": {},
                "missing": [],
                "work_rooms": {},
                "gpus": [],
                "host": "cmd",
            }
        ),
        encoding="utf-8",
    )
    cleaned = host_binding.stretch_load(str(tmp_path))
    assert cleaned is not None
    tools = cleaned.get("tools") or {}
    rooms = cleaned.get("rooms") or {}
    assert "python" in tools
    assert "openai_api_key" not in tools
    assert "auth_token" not in rooms


def test_format_home_line_and_whoami(tmp_path: Path) -> None:
    home = str(tmp_path)
    census = host_binding.stretch_home(home, force=True)
    line = host_binding.stretch_format_line(home, census)
    assert line.startswith("This home:")
    assert "host=" in line
    who = host_binding.stretch_format_whoami(home, census)
    assert "This home" in who
    assert "Hardware" in who or "Tools" in who


def test_format_home_line_falls_back_without_census(tmp_path: Path) -> None:
    line = host_binding.stretch_format_line(str(tmp_path))
    # No census yet — dialect fallback still returns a host line
    assert "Host bridge" in line or line == "" or "This home" in line


def test_no_secret_values_in_saved_json(tmp_path: Path) -> None:
    host_binding.stretch_home(str(tmp_path), force=True)
    raw = json.loads((tmp_path / "host" / "home.json").read_text(encoding="utf-8"))
    blob = json.dumps(raw).lower()
    assert "api_key" not in blob
    assert "sk-" not in blob


def test_rooms_only_existing_dirs(tmp_path: Path) -> None:
    census = host_binding.stretch_home(str(tmp_path), force=True)
    rooms = census.get("rooms") or {}
    assert isinstance(rooms, dict)
    for path in rooms.values():
        assert Path(path).is_dir()

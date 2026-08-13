"""First-home stretch — census of hardware, tools, rooms, doors."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remedy.execution.host.stretch import (
    HomeCensus,
    census_path,
    format_home_line,
    format_home_whoami,
    load_census,
    needs_stretch,
    save_census,
    stretch_home,
)


def test_stretch_home_persists(tmp_path: Path) -> None:
    census = stretch_home(tmp_path, force=True)
    assert census.stretched_at
    assert census.os_name
    assert census.cpu_count >= 1
    assert "python" in census.tools
    path = census_path(tmp_path)
    assert path.is_file()
    loaded = load_census(tmp_path)
    assert loaded is not None
    assert loaded.os_name == census.os_name
    assert loaded.tools.get("python") == census.tools["python"]


def test_stretch_skips_when_fresh(tmp_path: Path) -> None:
    first = stretch_home(tmp_path, force=True)
    again = stretch_home(tmp_path, force=False)
    assert again.stretched_at == first.stretched_at


def test_needs_stretch_stale(tmp_path: Path) -> None:
    census = stretch_home(tmp_path, force=True)
    assert needs_stretch(tmp_path) is False
    old = datetime.now(UTC) - timedelta(days=20)
    census.stretched_at = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    save_census(census, tmp_path)
    assert needs_stretch(tmp_path) is True


def test_needs_stretch_missing(tmp_path: Path) -> None:
    assert needs_stretch(tmp_path) is True


def test_census_strips_secret_shaped_keys(tmp_path: Path) -> None:
    dirty = HomeCensus(
        stretched_at="2026-01-01T00:00:00Z",
        tools={"python": "/usr/bin/python", "api_key": "sk-secret"},
        rooms={"desktop": "C:/Users/x/Desktop"},
    )
    # from_dict is the persist/load gate
    cleaned = HomeCensus.from_dict(
        {
            **dirty.to_dict(),
            "tools": {"python": "/usr/bin/python", "openai_api_key": "sk-leak"},
            "rooms": {"desktop": "C:/ok", "auth_token": "nope"},
        }
    )
    assert "python" in cleaned.tools
    assert "openai_api_key" not in cleaned.tools
    assert "auth_token" not in cleaned.rooms


def test_format_home_line_and_whoami(tmp_path: Path) -> None:
    census = stretch_home(tmp_path, force=True)
    line = format_home_line(census, home=tmp_path)
    assert line.startswith("This home:")
    assert "host=" in line
    who = format_home_whoami(census, home=tmp_path)
    assert "This home" in who
    assert "Hardware" in who or "Tools" in who


def test_format_home_line_falls_back_without_census(tmp_path: Path) -> None:
    line = format_home_line(home=tmp_path)
    # No census yet — dialect fallback still returns a host line
    assert "Host bridge" in line or line == "" or "This home" in line


def test_no_secret_values_in_saved_json(tmp_path: Path) -> None:
    stretch_home(tmp_path, force=True)
    raw = json.loads(census_path(tmp_path).read_text(encoding="utf-8"))
    blob = json.dumps(raw).lower()
    assert "api_key" not in blob
    assert "sk-" not in blob


def test_rooms_only_existing_dirs(tmp_path: Path) -> None:
    census = stretch_home(tmp_path, force=True)
    for path in census.rooms.values():
        assert Path(path).is_dir()
    for path in census.work_rooms.values():
        assert Path(path).is_dir()

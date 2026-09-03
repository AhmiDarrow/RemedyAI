"""Write UIA JSON fixtures for Zig / host_binding parity (Phase 2 cutover).

Contract fixtures always come from fake_host_binding. Live capture runs on
Windows when UIA is available; failure still leaves contract JSON + README.
"""

from __future__ import annotations

from pathlib import Path

from tests.harness import uia_fixture_capture as cap

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "uia"


def test_write_uia_contract_and_optional_live_fixtures():
    contract = cap.write_harness_contract_fixtures()
    assert len(contract) >= 4
    for path in contract:
        assert path.is_file()
        assert path.stat().st_size > 20

    live = cap.try_live_capture()
    readme = cap.write_readme(
        live_ok=bool(live.get("ok")), live_error=live.get("error")
    )
    assert readme.is_file()
    assert (FIXTURE_DIR / "contract_uia_control_snapshot.json").is_file()
    assert (FIXTURE_DIR / "contract_read_window_text.json").is_file()
    assert (FIXTURE_DIR / "contract_focused_element_info.json").is_file()
    assert (FIXTURE_DIR / "contract_element_action.json").is_file()
    assert (FIXTURE_DIR / "contract_misc.json").is_file()

    # Live is best-effort: if it succeeded, the three live files exist.
    if live.get("ok"):
        assert (FIXTURE_DIR / "live_uia_control_snapshot.json").is_file()
        assert (FIXTURE_DIR / "live_read_window_text.json").is_file()
        assert (FIXTURE_DIR / "live_focused_element_info.json").is_file()

"""Windows reserved device name guards."""

from remedy.core.win_paths import (
    check_tool_path_safe,
    is_windows_reserved_name,
    path_has_windows_reserved_segment,
)


def test_reserved_basenames():
    assert is_windows_reserved_name("nul")
    assert is_windows_reserved_name("NUL")
    assert is_windows_reserved_name("con.txt")
    assert is_windows_reserved_name("COM1")
    assert not is_windows_reserved_name("null")
    assert not is_windows_reserved_name("main.gd")


def test_reserved_in_path():
    assert path_has_windows_reserved_segment(r"C:\proj\nul") == "nul"
    assert path_has_windows_reserved_segment(r"C:\proj\scripts\main.gd") is None
    msg = check_tool_path_safe(r"C:\Users\x\FallenEarth\nul")
    assert msg and "reserved" in msg.lower()

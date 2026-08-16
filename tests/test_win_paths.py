"""Windows reserved device name guards (must pass on Linux CI and Windows)."""

from remedy.core.win_paths import (
    check_tool_path_safe,
    is_windows_reserved_name,
    path_has_windows_reserved_segment,
    reserved_device_basename,
)


def test_reserved_basenames():
    assert is_windows_reserved_name("nul")
    assert is_windows_reserved_name("NUL")
    assert is_windows_reserved_name("con.txt")
    assert is_windows_reserved_name("COM1")
    assert not is_windows_reserved_name("null")
    assert not is_windows_reserved_name("main.gd")
    assert reserved_device_basename("CON.txt") == "CON"
    assert reserved_device_basename("nul") == "NUL"


def test_reserved_in_path_windows_style():
    """Backslash paths must resolve to the short device name on any OS."""
    assert path_has_windows_reserved_segment(r"C:\proj\nul") == "NUL"
    assert path_has_windows_reserved_segment(r"C:\proj\scripts\main.gd") is None
    assert path_has_windows_reserved_segment(r"C:\Users\x\FallenEarth\nul") == "NUL"
    msg = check_tool_path_safe(r"C:\Users\x\FallenEarth\nul")
    assert msg and "reserved" in msg.lower()
    assert "NUL" in msg


def test_reserved_in_path_posix_style():
    """Forward-slash and mixed paths (Linux CI checking Windows trees)."""
    assert path_has_windows_reserved_segment("C:/proj/nul") == "NUL"
    assert path_has_windows_reserved_segment("/mnt/c/proj/nul") == "NUL"
    assert path_has_windows_reserved_segment("proj/con.txt") == "CON"
    assert path_has_windows_reserved_segment("/home/user/main.gd") is None
    assert path_has_windows_reserved_segment("proj/null/file.py") is None


def test_reserved_com_lpt():
    assert path_has_windows_reserved_segment(r"D:\out\COM3") == "COM3"
    assert path_has_windows_reserved_segment("build/LPT1.log") == "LPT1"

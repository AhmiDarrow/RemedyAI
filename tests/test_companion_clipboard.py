"""Companion clipboard/foreground via Zig ``host_binding`` (no Win32 ctypes)."""

from __future__ import annotations

import os

import pytest

from remedy.core import companion as C

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 clipboard only")


def test_ctypes_clipboard_helpers_are_gone():
    """HDROP/DIB/foreground prototypes lived here; Zig owns those reads now."""
    assert not hasattr(C, "_declare_win32_clipboard_prototypes")
    assert not hasattr(C, "_WIN32_PROTOTYPES_SET")
    assert not hasattr(C, "_CF_HDROP")
    assert not hasattr(C, "_CF_DIB")
    assert not hasattr(C, "_CF_UNICODETEXT")
    assert not hasattr(C, "_dib_to_png_bytes")
    assert not hasattr(C, "_PROCESS_QUERY_LIMITED")


# --- the calls themselves ---------------------------------------------------
# Read-only. Nothing here writes to the owner's clipboard.


def test_reading_the_clipboard_does_not_crash_the_process():
    """It used to. Reaching the assert at all is the result."""
    out = C.Win32CompanionBackend().clipboard_text()
    assert out is None or isinstance(out, str)


def test_a_clipboard_read_is_not_truncated_at_an_embedded_terminator():
    """Host text path must not leave NULs in the returned string."""
    out = C.Win32CompanionBackend().clipboard_text()
    if out:
        assert "\x00" not in out


def test_listing_clipboard_files_does_not_crash():
    assert isinstance(C.Win32CompanionBackend().clipboard_files(), list)


def test_reading_a_clipboard_image_does_not_crash():
    out = C.Win32CompanionBackend().clipboard_image_png()
    assert out is None or isinstance(out, bytes)
    if out:
        assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_foreground_detail_includes_pid_keys():
    fg = C.Win32CompanionBackend().foreground()
    assert isinstance(fg, dict)
    if fg:
        assert "hwnd" in fg and "title" in fg
        assert "pid" in fg and "exe" in fg and "exe_name" in fg
        assert isinstance(fg["pid"], int)
        assert isinstance(fg["exe"], str)


def test_the_full_snapshot_does_not_crash():
    assert isinstance(C.gather_companion_snapshot(), dict)

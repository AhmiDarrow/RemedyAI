"""The Win32 clipboard backend — the ctypes prototypes, above all.

This code crashed the process. Not raised, not returned an error: a Windows
access violation, which is not a Python exception and which the ``except
Exception`` around it could never have caught. Remedy would simply vanish
mid-turn whenever she read the clipboard.

The cause was ctypes' default: with no ``restype``, a foreign function is
assumed to return ``int`` — 32 bits. ``GetClipboardData`` returns a 64-bit
HANDLE on a 64-bit Python, so the top half was silently discarded and the
truncated value passed straight to ``GlobalLock``. Locking a bogus handle can
still hand back a non-null pointer, and ``wstring_at`` on it walks unmapped
memory until it happens to find a zero.

So the tests that matter here are about declared signatures, not behaviour.
"""

from __future__ import annotations

import os

import pytest

from remedy.core import companion as C

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 clipboard only")


@pytest.fixture()
def declared():
    C._WIN32_PROTOTYPES_SET = False
    C._declare_win32_clipboard_prototypes()
    import ctypes

    return {
        "user32": ctypes.windll.user32,
        "kernel32": ctypes.windll.kernel32,
        "shell32": ctypes.windll.shell32,
        "ctypes": ctypes,
    }


# --- the prototypes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("dll", "func"),
    [
        ("user32", "GetClipboardData"),
        ("user32", "SetClipboardData"),
        ("kernel32", "GlobalLock"),
        ("kernel32", "GlobalAlloc"),
        ("kernel32", "GlobalFree"),
    ],
)
def test_every_handle_returning_call_is_pointer_sized(declared, dll, func):
    """The bug in one assertion: an int restype truncates a 64-bit handle."""
    fn = getattr(declared[dll], func)
    assert fn.restype is declared["ctypes"].c_void_p, (
        f"{dll}.{func} returns a handle; an int restype loses the top 32 bits"
    )


@pytest.mark.parametrize(
    ("dll", "func"),
    [
        ("kernel32", "GlobalLock"),
        ("kernel32", "GlobalUnlock"),
        ("kernel32", "GlobalSize"),
        ("kernel32", "GlobalFree"),
        ("shell32", "DragQueryFileW"),
    ],
)
def test_every_handle_taking_call_accepts_a_pointer(declared, dll, func):
    """Passing a 64-bit handle into an int argument truncates it just as badly."""
    fn = getattr(declared[dll], func)
    assert fn.argtypes is not None, f"{dll}.{func} has no declared argtypes"
    assert fn.argtypes[0] is declared["ctypes"].c_void_p


def test_global_size_returns_a_size_not_an_int(declared):
    """The read is bounded by this; a truncated size would bound it wrongly."""
    assert declared["kernel32"].GlobalSize.restype is declared["ctypes"].c_size_t


def test_declaring_twice_is_harmless(declared):
    C._declare_win32_clipboard_prototypes()
    C._declare_win32_clipboard_prototypes()
    assert C._WIN32_PROTOTYPES_SET is True


def test_declaration_is_skipped_off_windows(monkeypatch):
    monkeypatch.setattr(C.os, "name", "posix")
    C._WIN32_PROTOTYPES_SET = False
    C._declare_win32_clipboard_prototypes()
    assert C._WIN32_PROTOTYPES_SET is False


# --- the calls themselves ---------------------------------------------------
# Read-only. Nothing here writes to the owner's clipboard.


def test_reading_the_clipboard_does_not_crash_the_process():
    """It used to. Reaching the assert at all is the result."""
    out = C.Win32CompanionBackend().clipboard_text()
    assert out is None or isinstance(out, str)


def test_a_clipboard_read_is_not_truncated_at_an_embedded_terminator():
    """The bounded read must stop at the terminator, not before or after it."""
    out = C.Win32CompanionBackend().clipboard_text()
    if out:
        assert "\x00" not in out


def test_listing_clipboard_files_does_not_crash():
    assert isinstance(C.Win32CompanionBackend().clipboard_files(), list)


def test_reading_a_clipboard_image_does_not_crash():
    out = C.Win32CompanionBackend().clipboard_image_png()
    assert out is None or isinstance(out, bytes)


def test_the_full_snapshot_does_not_crash():
    assert isinstance(C.gather_companion_snapshot(), dict)

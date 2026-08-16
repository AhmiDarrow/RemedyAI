"""WinError 64 must count as disconnect so recovery runs (not 'Interrupted:')."""

from remedy.core.react_turn import is_disconnect_error


def test_winerror_64_is_disconnect():
    assert is_disconnect_error(
        OSError(64, "The specified network name is no longer available")
    )
    assert is_disconnect_error(
        "[WinError 64] The specified network name is no longer available"
    )
    assert is_disconnect_error("Server disconnected")
    assert is_disconnect_error("actively refused")


def test_real_errors_not_disconnect():
    assert not is_disconnect_error("file not found")
    assert not is_disconnect_error("PREFER_FILE_EDIT")

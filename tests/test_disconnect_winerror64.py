"""WinError 64 must count as disconnect so recovery runs (not 'Interrupted:')."""

from remedy.core.react_turn import is_connect_refused_error, is_disconnect_error


def test_winerror_64_is_disconnect():
    assert is_disconnect_error(
        OSError(64, "The specified network name is no longer available")
    )
    assert is_disconnect_error(
        "[WinError 64] The specified network name is no longer available"
    )
    assert is_disconnect_error("Server disconnected")
    assert is_disconnect_error("actively refused")
    assert is_disconnect_error("Error: network error")
    assert is_disconnect_error("network error")
    # Live 2026-08-27 grok-4.6 SSE: aiohttp wraps the RST as ClientPayloadError.
    assert is_disconnect_error(
        "ClientPayloadError: Response payload is not completed: "
        "<TransferEncodingError: 400, message='Not enough data to satisfy "
        "transfer length header.'>"
    )


def test_rmb_refused_is_connect_error_not_a_stream_drop():
    """Live 2026-08-26: 8787 refused is 'RMB is off', not eight disconnect waits."""
    refused = (
        "Cannot connect to host 127.0.0.1:8787 ssl:default "
        "[The remote computer refused the network connection]"
    )
    assert is_connect_refused_error(refused) is True
    assert is_connect_refused_error("actively refused") is True
    assert is_connect_refused_error(
        "ClientPayloadError: Response payload is not completed"
    ) is False


def test_real_errors_not_disconnect():
    assert not is_disconnect_error("file not found")
    assert not is_disconnect_error("PREFER_FILE_EDIT")

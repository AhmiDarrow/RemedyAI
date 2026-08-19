"""MCP client — what happens when the server on the other end misbehaves.

An MCP server is someone else's process. It can exit mid-call, hang forever, or
answer with a malformed envelope, and none of that may leave Remedy waiting on
a future that will never resolve — a hung tool call stalls the whole turn with
no error anyone can read.

So the subject here is the failure side of the lifecycle: in-flight requests
are failed when the process goes, stale ones are swept, a disconnected
server's tools stop being offered, and a JSON-RPC error becomes an error rather
than a result. The happy path already has tests; this is the rest of it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from remedy.models import ToolCall, ToolSource
from remedy.tools.mcp_client import MCPClient


@pytest.fixture()
def client():
    return MCPClient()


def register(client, server: str, name: str, *, uri: str | None = None):
    """Register a tool the way discovery does — under mcp:{server}:{name}."""
    from remedy.models import ToolDefinition

    tool = ToolDefinition(
        name=name,
        description=f"{name} on {server}",
        source=ToolSource.MCP,
        parameters={},
    )
    if uri is not None:
        tool.uri = uri
    client._tools[f"mcp:{server}:{name}"] = tool
    return tool


#: ToolCall.id is a real UUID; a hand-written "c1" is rejected by the model.
CALL_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def call(name: str, **arguments):
    return ToolCall(
        id=CALL_ID, tool_name=name, arguments=arguments, source=ToolSource.MCP
    )


# --- unwrapping what the server said -----------------------------------------


def test_a_result_payload_is_returned_as_is():
    assert MCPClient._unwrap_jsonrpc({"result": {"content": "hi"}}) == {"content": "hi"}


def test_a_scalar_result_is_wrapped_so_callers_get_a_dict():
    assert MCPClient._unwrap_jsonrpc({"result": 42}) == {"value": 42}


def test_an_error_object_becomes_an_error_not_a_result():
    """Returning the error dict as a result is how a failure reads as success."""
    out = MCPClient._unwrap_jsonrpc({"error": {"code": -32601, "message": "no method"}})
    assert out == {"error": "no method"}


def test_an_error_string_becomes_an_error_too():
    assert MCPClient._unwrap_jsonrpc({"error": "exploded"}) == {"error": "exploded"}


def test_an_error_with_no_message_still_reads_as_an_error():
    out = MCPClient._unwrap_jsonrpc({"error": {"code": -1}})
    assert "error" in out


def test_a_non_dict_message_is_wrapped():
    assert MCPClient._unwrap_jsonrpc("just a string") == {"value": "just a string"}
    assert MCPClient._unwrap_jsonrpc(None) == {"value": None}


def test_an_already_unwrapped_payload_passes_through():
    assert MCPClient._unwrap_jsonrpc({"content": "hi"}) == {"content": "hi"}


def test_a_null_error_field_is_not_an_error():
    """Servers routinely send error: null alongside a real result."""
    assert MCPClient._unwrap_jsonrpc({"error": None, "result": {"a": 1}}) == {"a": 1}


# --- resolving which server owns a tool ---------------------------------------


def test_a_tool_resolves_to_the_server_that_registered_it(client):
    register(client, "files", "read_file")
    tool, server = client._resolve_tool(call("read_file"))
    assert tool is not None
    assert server == "files"


def test_an_explicit_server_hint_wins(client):
    """Two servers can offer the same tool name; the hint disambiguates."""
    register(client, "alpha", "search")
    register(client, "beta", "search")
    _, server = client._resolve_tool(call("search", _mcp_server="beta"))
    assert server == "beta"


def test_a_hint_at_a_server_that_does_not_have_it_falls_back(client):
    register(client, "alpha", "search")
    tool, server = client._resolve_tool(call("search", _mcp_server="nowhere"))
    assert tool is not None
    assert server == "alpha"


def test_an_unknown_tool_resolves_to_nothing(client):
    assert client._resolve_tool(call("no_such_tool")) == (None, None)


def test_a_tool_carrying_a_uri_resolves_through_it(client):
    from remedy.models import ToolDefinition

    tool = ToolDefinition(
        name="fetch", description="", source=ToolSource.MCP, parameters={}
    )
    tool.uri = "mcp://web/fetch"
    client._tools["some-other-key"] = tool
    _, server = client._resolve_tool(call("fetch"))
    assert server == "web"


@pytest.mark.asyncio
async def test_calling_an_unknown_tool_returns_an_error_not_an_exception(client):
    result = await client.call_tool(call("no_such_tool"))
    assert result.error
    assert str(result.call_id) == str(CALL_ID)


# --- the registry -------------------------------------------------------------


def test_a_registered_tool_is_listed(client):
    client.register_tool("local_thing", "does a thing")
    assert [t.name for t in client.list_tools()] == ["local_thing"]


def test_a_tool_can_be_fetched_by_name(client):
    register(client, "files", "read_file")
    assert client.get_tool("read_file") is not None


def test_a_tool_can_be_fetched_from_a_named_server(client):
    register(client, "alpha", "search")
    register(client, "beta", "search")
    assert client.get_tool("search", server="beta").description.endswith("beta")


def test_fetching_from_the_wrong_server_finds_nothing(client):
    register(client, "alpha", "search")
    assert client.get_tool("search", server="beta") is None


def test_fetching_a_tool_nobody_offers_finds_nothing(client):
    assert client.get_tool("nothing") is None


# --- purging a server's tools -------------------------------------------------


def test_disconnecting_stops_a_servers_tools_being_offered(client):
    """A tool still listed after its server left is a call that cannot land."""
    register(client, "files", "read_file")
    register(client, "files", "write_file")
    register(client, "web", "fetch")
    assert client._purge_server_tools("files") == 2
    assert [t.name for t in client.list_tools()] == ["fetch"]


def test_purging_a_server_that_was_never_connected_removes_nothing(client):
    register(client, "web", "fetch")
    assert client._purge_server_tools("nowhere") == 0
    assert len(client.list_tools()) == 1


def test_a_tool_registered_by_uri_is_purged_too(client):
    register(client, "web", "fetch", uri="mcp://web/fetch")
    assert client._purge_server_tools("web") == 1


def test_a_similarly_named_server_is_not_caught_by_the_purge(client):
    """`files` and `files-extra` are different servers."""
    register(client, "files-extra", "read_file")
    assert client._purge_server_tools("files") == 0
    assert len(client.list_tools()) == 1


# --- in-flight requests when the server goes ----------------------------------


@pytest.mark.asyncio
async def test_a_pending_request_is_failed_when_the_server_disconnects(client):
    """Otherwise the turn waits forever on a future nothing will resolve."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client._pending[1] = fut
    client._pending_times[1] = 0.0

    assert client._fail_pending() == 1
    with pytest.raises(ConnectionError):
        await fut


@pytest.mark.asyncio
async def test_the_failure_says_why(client):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client._pending[1] = fut
    client._pending_times[1] = 0.0
    client._fail_pending("server crashed")
    with pytest.raises(ConnectionError, match="server crashed"):
        await fut


@pytest.mark.asyncio
async def test_an_already_finished_request_is_not_failed_again(client):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result({"ok": True})
    client._pending[1] = fut
    client._pending_times[1] = 0.0
    assert client._fail_pending() == 0
    assert await fut == {"ok": True}


def test_failing_with_nothing_in_flight_is_fine(client):
    assert client._fail_pending() == 0


@pytest.mark.asyncio
async def test_the_pending_table_is_emptied(client):
    loop = asyncio.get_running_loop()
    for i in range(3):
        client._pending[i] = loop.create_future()
        client._pending_times[i] = 0.0
    futures = list(client._pending.values())
    client._fail_pending()
    assert client._pending == {}
    assert client._pending_times == {}
    # Retrieve every exception so none is reported as never-retrieved on GC.
    await asyncio.gather(*futures, return_exceptions=True)


# --- hung servers -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_older_than_the_ceiling_is_swept(client):
    """A server that accepted the request and went quiet must not hold the turn."""
    import time

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client._pending[1] = fut
    client._pending_times[1] = time.monotonic() - (client._STALE_PENDING_SECONDS + 5)

    assert client._sweep_stale_pending() == 1
    with pytest.raises(ConnectionError, match="stale"):
        await fut


@pytest.mark.asyncio
async def test_a_recent_request_is_left_alone(client):
    """Sweeping too eagerly kills calls that were about to succeed."""
    import time

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client._pending[1] = fut
    client._pending_times[1] = time.monotonic()

    assert client._sweep_stale_pending() == 0
    assert not fut.done()
    fut.cancel()


def test_sweeping_with_nothing_in_flight_is_fine(client):
    assert client._sweep_stale_pending() == 0


@pytest.mark.asyncio
async def test_a_request_with_no_recorded_start_time_is_left_alone(client):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client._pending[1] = fut  # no _pending_times entry
    assert client._sweep_stale_pending() == 0
    fut.cancel()


# --- disconnecting ------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnecting_a_server_that_was_never_connected_is_not_an_error(client):
    await client.disconnect("nowhere")


@pytest.mark.asyncio
async def test_disconnect_all_on_a_fresh_client_is_not_an_error(client):
    await client.disconnect_all()

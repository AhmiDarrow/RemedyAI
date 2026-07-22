"""MCP Client — full Model Context Protocol integration.

Connects to MCP servers via stdio, lists tools/resources/prompts,
and invokes them on behalf of the agent runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from remedy.models import ToolCall, ToolDefinition, ToolResult, ToolSource

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for Model Context Protocol (MCP) servers.

    Communicates with external MCP servers via stdio/JSON-RPC.
    Each server is a subprocess; the client sends requests and
    receives responses over stdin/stdout.

    Reference: https://modelcontextprotocol.io
    """

    def __init__(self) -> None:
        self._servers: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id: int = 1

    # -- server management ---------------------------------------------------

    async def connect(
        self,
        server_name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> bool:
        """Spawn an MCP server subprocess and handshake."""
        if server_name in self._servers:
            await self.disconnect(server_name)

        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *(args or []),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            self._servers[server_name] = {
                "command": command,
                "args": args or [],
                "connected": True,
            }
            self._processes[server_name] = proc

            # Start reader
            self._readers[server_name] = asyncio.create_task(
                self._read_responses(server_name, proc)
            )

            # Send initialize
            result = await self._send_request(server_name, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Remedy", "version": "0.7.0"},
            })

            if result.get("error"):
                logger.error("MCP init failed for %s: %s", server_name, result["error"])
                return False

            return True
        except Exception as e:
            logger.error("MCP connect failed: %s", e)
            return False

    async def disconnect(self, server_name: str) -> None:
        """Gracefully disconnect from an MCP server."""
        if server_name in self._readers:
            self._readers[server_name].cancel()
            del self._readers[server_name]

        proc = self._processes.pop(server_name, None)
        if proc:
            try:
                proc.stdin.close()
                proc.kill()
                await proc.wait()
            except Exception:
                pass

        self._servers.pop(server_name, None)

    async def disconnect_all(self) -> None:
        for name in list(self._servers.keys()):
            await self.disconnect(name)

    # -- tool discovery ------------------------------------------------------

    async def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """Fetch tool list from an MCP server and register them."""
        result = await self._send_request(server_name, "tools/list", {})
        tools_raw = result.get("tools", [])

        discovered: list[ToolDefinition] = []
        for tool_raw in tools_raw:
            name = tool_raw.get("name", "unknown")
            desc = tool_raw.get("description", "")
            params = tool_raw.get("inputSchema", tool_raw.get("parameters", {}))

            tool = ToolDefinition(
                name=name,
                description=desc,
                source=ToolSource.MCP,
                parameters=params,
                uri=f"mcp://{server_name}/{name}",
            )
            self._tools[f"mcp:{server_name}:{name}"] = tool
            discovered.append(tool)

        logger.info("MCP %s: discovered %d tools", server_name, len(discovered))
        return discovered

    # -- tool invocation -----------------------------------------------------

    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        """Invoke a tool on an MCP server."""
        key = f"mcp:{tool_call.source}:{tool_call.tool_name}"
        tool = self._tools.get(key)

        if tool is None:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=f"MCP tool not found: {tool_call.tool_name}",
            )

        server_name = tool_call.source
        if server_name not in self._servers:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=f"MCP server not connected: {server_name}",
            )

        import time
        start = time.monotonic()

        result = await self._send_request(
            server_name,
            "tools/call",
            {
                "name": tool_call.tool_name,
                "arguments": tool_call.arguments,
            },
            timeout=30.0,
        )

        elapsed = (time.monotonic() - start) * 1000

        if result.get("error"):
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=result.get("error", "MCP tool error"),
                duration_ms=elapsed,
            )

        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", json.dumps(content))
        else:
            text = json.dumps(content)

        return ToolResult(
            call_id=tool_call.id,
            success=True,
            data={"result": text, "raw": result},
            duration_ms=elapsed,
        )

    # -- tool registration (non-MCP sources) ---------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        source: ToolSource = ToolSource.MCP,
    ) -> ToolDefinition:
        tool = ToolDefinition(
            name=name,
            description=description,
            source=source,
            parameters=parameters or {},
        )
        key = f"{source.value}:{name}"
        self._tools[key] = tool
        return tool

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_tool(self, name: str, server: str | None = None) -> ToolDefinition | None:
        if server:
            return self._tools.get(f"mcp:{server}:{name}")
        for key, tool in self._tools.items():
            if tool.name == name:
                return tool
        return None

    # -- resource & prompt discovery (stubs) ---------------------------------

    async def list_resources(self, server_name: str) -> list[dict]:
        result = await self._send_request(server_name, "resources/list", {})
        return result.get("resources", [])

    async def list_prompts(self, server_name: str) -> list[dict]:
        result = await self._send_request(server_name, "prompts/list", {})
        return result.get("prompts", [])

    # -- JSON-RPC transport --------------------------------------------------

    async def _send_request(
        self,
        server_name: str,
        method: str,
        params: dict[str, Any],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1

        proc = self._processes.get(server_name)
        if proc is None or proc.stdin is None:
            return {"error": f"Server not connected: {server_name}"}

        message = json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }) + "\n"

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = fut

        try:
            proc.stdin.write(message.encode("utf-8"))
            await proc.stdin.drain()
        except Exception as e:
            self._pending.pop(request_id, None)
            return {"error": str(e)}

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except TimeoutError:
            self._pending.pop(request_id, None)
            return {"error": f"Request timed out after {timeout}s"}
        except Exception as e:
            self._pending.pop(request_id, None)
            return {"error": str(e)}

    async def _read_responses(self, server_name: str, proc: asyncio.subprocess.Process) -> None:
        """Continuously read JSON-RPC responses from a server's stdout."""
        try:
            while proc.stdout and not proc.stdout.at_eof():
                line = await proc.stdout.readline()
                if not line:
                    break

                try:
                    data = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                msg_id = data.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MCP reader error for %s", server_name)
        finally:
            self._servers.pop(server_name, None)

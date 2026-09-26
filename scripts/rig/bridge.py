"""Let an external agent drive Remedy's real runtime through a file mailbox.

Run ``python -m rig.bridge --directory out/bridge --port 8799``. Each model
request appears as ``000001.request.json``; the supervising agent writes
``000001.response.json`` containing an OpenAI assistant message (``content``
and/or ``tool_calls``). This is a transport, not a scripted task solver.
Only synthetic evaluation workspaces should be used: request files contain
the complete prompt and tool results. No authorization headers are saved.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def completion_message(value: object) -> dict[str, Any]:
    """Validate the mailbox boundary before any external-agent tool call executes."""
    if not isinstance(value, dict):
        raise ValueError("Agent response must be an assistant message object")
    content = value.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("Agent response content must be text or null")
    calls = value.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("Agent tool_calls must be an array")
    seen: set[str] = set()
    normalized = []
    for call in calls:
        if not isinstance(call, dict) or call.get("type", "function") != "function":
            raise ValueError("Each agent tool call must be a function call")
        call_id = call.get("id")
        function = call.get("function")
        if not isinstance(call_id, str) or not call_id.strip() or call_id in seen:
            raise ValueError("Agent tool calls require unique nonempty ids")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ValueError("Agent tool calls require a function name")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("Agent function arguments must be a JSON object string")
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise ValueError("Agent function arguments must be valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("Agent function arguments must decode to an object")
        seen.add(call_id)
        normalized.append({"id": call_id, "type": "function", "function": {
            "name": function["name"], "arguments": arguments,
        }})
    result: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if normalized:
        result["tool_calls"] = normalized
    return result


def make_server(directory: Path, port: int, timeout: float = 600) -> ThreadingHTTPServer:
    directory.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    # Resume after both requests and responses: reusing an old ID can replay a
    # stale tool call, besides destroying the evidence from the previous run.
    sequence = max((int(path.name.split(".")[0]) for path in directory.glob("*.json")
                    if path.name.split(".")[0].isdigit()), default=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path not in {"/v1/models", "/models"}:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"codex-bridge","object":"model"}]}')

        def do_POST(self) -> None:
            nonlocal sequence
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid Content-Length")
                return
            if not 0 < size <= 16 * 1024 * 1024:
                self.send_error(413)
                return
            try:
                request = json.loads(self.rfile.read(size))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON request")
                return
            if not isinstance(request, dict):
                self.send_error(400, "Expected a JSON object")
                return
            with lock:
                sequence += 1
                stem = f"{sequence:06d}"
            target = directory / f"{stem}.request.json"
            temporary = directory / f"{stem}.tmp"
            temporary.write_text(json.dumps(request, indent=2), encoding="utf-8")
            temporary.replace(target)
            print(f"waiting {target}", flush=True)
            reply = directory / f"{stem}.response.json"
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if reply.is_file():
                    try:
                        message = json.loads(reply.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        time.sleep(0.1)
                        continue
                    break
                time.sleep(0.1)
            else:
                self.send_error(504, "No agent response before the deadline")
                return
            try:
                assistant = completion_message(message)
            except ValueError as error:
                self.send_error(502, str(error))
                return
            calls = assistant.get("tool_calls", [])
            if not request.get("stream", False):
                body = json.dumps({
                    "id": f"bridge-{stem}", "object": "chat.completion",
                    "choices": [{"index": 0, "message": assistant,
                                 "finish_reason": "tool_calls" if calls else "stop"}],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    print(f"request {stem} disconnected", flush=True)
                return
            delta = dict(assistant)
            if calls:
                delta["tool_calls"] = [dict(call, index=i) for i, call in enumerate(calls)]
            chunk = {
                "id": f"bridge-{stem}", "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta,
                             "finish_reason": "tool_calls" if calls else "stop"}],
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                print(f"request {stem} disconnected", flush=True)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(directory: Path, port: int, timeout: float = 600) -> None:
    server = make_server(directory, port, timeout)
    print(f"bridge http://127.0.0.1:{server.server_port}/v1", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    serve(args.directory, args.port)

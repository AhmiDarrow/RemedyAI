"""A2A v1.0 JSON-RPC server and outbound client.

Method names are PascalCase (SendMessage, GetTask, …). v0.3 slash-form
names (message/send, tasks/get, …) are accepted as aliases so older
clients still work. Enums are emitted as TASK_STATE_*.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from remedy.interop.card import A2A_PROTOCOL_VERSION, WELL_KNOWN_PATH, public_card
from remedy.interop.language import for_peer, inbound_plain

# v1.0 method names → also accept the v0.3 slash-form aliases.
METHODS = {
    "SendMessage": "SendMessage",
    "message/send": "SendMessage",
    "SendStreamingMessage": "SendStreamingMessage",
    "message/stream": "SendStreamingMessage",
    "GetTask": "GetTask",
    "tasks/get": "GetTask",
    "ListTasks": "ListTasks",
    "tasks/list": "ListTasks",
    "CancelTask": "CancelTask",
    "tasks/cancel": "CancelTask",
    "GetExtendedAgentCard": "GetExtendedAgentCard",
    "agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
    "SubscribeToTask": "SubscribeToTask",
    "tasks/resubscribe": "SubscribeToTask",
}

ERR_TASK_NOT_FOUND = -32001
ERR_TASK_NOT_CANCELABLE = -32002
ERR_UNSUPPORTED = -32004
ERR_CONTENT_TYPE = -32005
ERR_VERSION = -32009


def rpc_error(mid: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


def rpc_result(mid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def extract_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts") or []
    chunks: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            if "text" in part:
                chunks.append(str(part.get("text") or ""))
            elif part.get("kind") == "text":
                chunks.append(str(part.get("text") or ""))
    if not chunks and message.get("text"):
        chunks.append(str(message.get("text") or ""))
    return inbound_plain("\n".join(chunks).strip())


def text_message(text: str, *, role: str = "agent", context_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    english, structured = for_peer(text)
    parts: list[dict[str, Any]] = [{"text": english}]
    if structured:
        parts.append({"data": structured})
    msg: dict[str, Any] = {
        "messageId": str(uuid.uuid4()),
        "role": role,
        "parts": parts,
    }
    if context_id:
        msg["contextId"] = context_id
    if metadata:
        msg["metadata"] = metadata
    return msg


def requested_version(headers: dict[str, str] | None, params: dict[str, Any] | None = None) -> str:
    if headers:
        for key, value in headers.items():
            if key.lower() == "a2a-version" and str(value).strip():
                return str(value).strip()
    if params and params.get("A2A-Version"):
        return str(params.get("A2A-Version")).strip()
    return A2A_PROTOCOL_VERSION


def version_ok(version: str) -> bool:
    if not version:
        return True
    major = version.split(".")[0]
    return major in {"0", "1"} and version.split(".")[0:2] != ["2"]


class A2AHandler:
    """Dispatch one JSON-RPC request against an InteropRuntime-like object."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def handle(self, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        mid = body.get("id")
        method = METHODS.get(str(body.get("method") or ""))
        params = body.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        version = requested_version(headers, params)
        if not version_ok(version):
            return rpc_error(mid, ERR_VERSION, f"VersionNotSupportedError: {version}")
        if method is None:
            return rpc_error(mid, -32601, f"Method not found: {body.get('method')}")
        try:
            result = getattr(self, f"_do_{method}")(params, headers or {})
        except KeyError as exc:
            if str(exc) == "'not_found'":
                return rpc_error(mid, ERR_TASK_NOT_FOUND, "TaskNotFoundError")
            if str(exc) == "'not_cancelable'":
                return rpc_error(mid, ERR_TASK_NOT_CANCELABLE, "TaskNotCancelableError")
            raise
        except PermissionError as exc:
            return rpc_error(mid, -32000, str(exc) or "permission denied")
        except ValueError as exc:
            return rpc_error(mid, -32602, str(exc))
        return rpc_result(mid, result)

    def _do_SendMessage(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.runtime.a2a_send(params, stream=False, headers=headers)

    def _do_SendStreamingMessage(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.runtime.a2a_send(params, stream=True, headers=headers)

    def _do_GetTask(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        task_id = str(params.get("id") or params.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("id is required")
        task = self.runtime.get_task(task_id)
        if task is None:
            raise KeyError("not_found")
        return task

    def _do_ListTasks(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        page, token = self.runtime.list_tasks(
            page_size=int(params.get("pageSize") or 20),
            page_token=params.get("pageToken") or params.get("page_token"),
            state=params.get("status") or params.get("state"),
            context_id=params.get("contextId") or params.get("context_id"),
        )
        out: dict[str, Any] = {"tasks": page}
        if token:
            out["nextPageToken"] = token
        return out

    def _do_CancelTask(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        task_id = str(params.get("id") or params.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("id is required")
        task, err = self.runtime.cancel_task(task_id)
        if err == "not_found" or task is None:
            raise KeyError("not_found")
        if err == "not_cancelable":
            raise KeyError("not_cancelable")
        return task

    def _do_GetExtendedAgentCard(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.runtime.extended_card()

    def _do_SubscribeToTask(self, params: dict[str, Any], headers: dict[str, str]) -> Any:
        # Same payload as GetTask; streaming is handled by the HTTP layer.
        return self._do_GetTask(params, headers)


class A2AClient:
    """Outbound A2A client. Discovers a peer via its Agent Card, then SendMessage."""

    def __init__(self, *, timeout: float = 30.0, opener=None) -> None:
        self.timeout = timeout
        self._opener = opener  # injectable for tests: (req) -> bytes

    def discover(self, base_url: str, *, token: str | None = None) -> dict[str, Any]:
        root = base_url.rstrip("/")
        urls = [
            f"{root}{WELL_KNOWN_PATH}" if "://" in root else root,
        ]
        # If the caller passed a site origin, try the well-known path.
        if not root.endswith("agent-card.json") and not root.endswith("agent.json"):
            if "://" in root:
                origin = root
                # strip a trailing /a2a or /a2a/v1
                for suffix in ("/a2a/v1", "/a2a"):
                    if origin.endswith(suffix):
                        origin = origin[: -len(suffix)]
                urls = [
                    f"{origin}{WELL_KNOWN_PATH}",
                    f"{origin}/.well-known/agent.json",
                    f"{root}{WELL_KNOWN_PATH}",
                ]
        last_err: Exception | None = None
        for url in urls:
            try:
                return self._get_json(url, token=token)
            except Exception as exc:
                last_err = exc
                continue
        raise last_err or URLError("agent card not found")

    def endpoint_from_card(self, card: dict[str, Any], fallback: str) -> str:
        interfaces = card.get("supportedInterfaces") or []
        if isinstance(interfaces, list):
            for item in interfaces:
                if isinstance(item, dict) and item.get("url"):
                    return str(item["url"])
        # v0.3 cards used a top-level url field.
        if card.get("url"):
            return str(card["url"])
        return fallback

    def send_message(
        self,
        endpoint: str,
        text: str,
        *,
        token: str | None = None,
        metadata: dict[str, Any] | None = None,
        context_id: str | None = None,
        return_immediately: bool = True,
    ) -> dict[str, Any]:
        english, structured = for_peer(text)
        parts: list[dict[str, Any]] = [{"text": english}]
        if structured:
            parts.append({"data": structured})
        message: dict[str, Any] = {
            "messageId": str(uuid.uuid4()),
            "role": "user",
            "parts": parts,
        }
        if context_id:
            message["contextId"] = context_id
        if metadata:
            message["metadata"] = metadata
        params = {
            "message": message,
            "configuration": {"return_immediately": return_immediately},
        }
        return self.rpc(endpoint, "SendMessage", params, token=token)

    def get_task(self, endpoint: str, task_id: str, *, token: str | None = None) -> dict[str, Any]:
        return self.rpc(endpoint, "GetTask", {"id": task_id}, token=token)

    def rpc(self, endpoint: str, method: str, params: dict[str, Any], *, token: str | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "A2A-Version": A2A_PROTOCOL_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = self._open(endpoint, data=data, headers=headers)
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("invalid JSON-RPC response")
        if body.get("error"):
            err = body["error"]
            raise RuntimeError(str((err or {}).get("message") or err))
        return body.get("result") if isinstance(body.get("result"), dict) else body

    def _get_json(self, url: str, *, token: str | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json", "A2A-Version": A2A_PROTOCOL_VERSION}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = self._open(url, data=None, headers=headers)
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("invalid agent card")
        return body

    def _open(self, url: str, *, data: bytes | None, headers: dict[str, str]) -> bytes:
        if self._opener is not None:
            return self._opener(url, data, headers)
        req = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except HTTPError as exc:
            raise RuntimeError(f"A2A HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"A2A request failed: {exc.reason}") from exc


# Re-export for callers that only want a card helper nearby.
agent_card = public_card

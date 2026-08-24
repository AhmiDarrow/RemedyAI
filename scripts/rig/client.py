"""HTTP + SSE client that drives one Remedy turn and records what happened.

The streaming endpoint emits ``tool_call`` / ``tool_result`` / ``token`` /
``usage`` / ``done`` events, so a turn can be scored on behaviour (did it call
the right tools, in what order, did they succeed) rather than on prose alone.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    preview: str = ""
    at: float = 0.0


@dataclass
class Turn:
    """Everything one send produced."""

    prompt: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    error: str = ""
    seconds: float = 0.0
    first_event_s: float | None = None
    first_tool_s: float | None = None
    session_id: str = ""

    # -- derived views used by scenario checks ---------------------------

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tool_calls]

    def called(self, *names: str) -> bool:
        wanted = {n.lower() for n in names}
        return any(t.name.lower() in wanted for t in self.tool_calls)

    def calls_to(self, *names: str) -> list[ToolCall]:
        wanted = {n.lower() for n in names}
        return [t for t in self.tool_calls if t.name.lower() in wanted]

    def succeeded(self, *names: str) -> bool:
        return any(t.ok is not False for t in self.calls_to(*names))

    @property
    def failed_tools(self) -> list[ToolCall]:
        return [t for t in self.tool_calls if t.ok is False]

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not self.error


class RemedyClient:
    """Minimal authenticated client for the local Remedy API."""

    def __init__(self, base: str, token: str, *, timeout: float = 600.0) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- plain JSON ------------------------------------------------------

    def api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return e.code, {"detail": raw}
        except Exception as e:  # connection refused, timeout, ...
            return 0, {"detail": str(e)}

    def new_session(
        self,
        title: str = "rig",
        project_path: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        """Create a session, optionally jailed to its own project folder."""
        body: dict[str, Any] = {"title": title}
        if project_path:
            body["project_path"] = project_path
        if provider:
            body["llm_provider"] = provider
        if model:
            body["model"] = model
        status, data = self.api("POST", "/api/sessions", body)
        if status not in (200, 201) or not isinstance(data, dict):
            raise RuntimeError(f"create session failed ({status}): {data}")
        sid = data.get("id") or data.get("session_id")
        if not sid:
            raise RuntimeError(f"create session returned no id: {data}")
        return str(sid)

    def abort(self, session_id: str) -> None:
        self.api("POST", f"/api/sessions/{session_id}/abort", {})

    # -- streaming turn --------------------------------------------------

    def send(
        self,
        session_id: str,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        timeout: float = 600.0,
        plan_mode: bool = False,
    ) -> Turn:
        """Send one message and consume the SSE stream to completion."""
        turn = Turn(prompt=message, session_id=session_id)
        body: dict[str, Any] = {"message": message, "plan_mode": plan_mode}
        if provider:
            body["provider"] = provider
        if model:
            body["model"] = model

        req = urllib.request.Request(
            f"{self.base}/api/sessions/{session_id}/messages/stream",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        start = time.time()
        deadline = start + float(timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for name, payload in _iter_sse(resp):
                    now = time.time() - start
                    # urlopen's timeout is a *socket* timeout: it resets on
                    # every byte. SSE keepalives therefore keep it alive
                    # forever, and a stuck turn ran 32 minutes against a 600s
                    # scenario limit. Enforce the wall clock ourselves.
                    if time.time() > deadline:
                        turn.status = "timeout"
                        turn.error = (
                            f"scenario wall-clock limit of {timeout:.0f}s exceeded "
                            f"after {len(turn.tool_calls)} tool call(s)"
                        )
                        break
                    if turn.first_event_s is None:
                        turn.first_event_s = now
                    turn.events.append({"event": name, "at": now, **payload})
                    self._apply(turn, name, payload, now)
                    if name in ("done", "error", "aborted"):
                        break
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            turn.error = f"HTTP {e.code}: {raw[:400]}"
            turn.status = "error"
        except Exception as e:
            turn.error = f"{type(e).__name__}: {e}"
            turn.status = "error"
        finally:
            turn.seconds = time.time() - start

        if not turn.status:
            turn.status = "incomplete"
        return turn

    @staticmethod
    def _apply(turn: Turn, name: str, payload: dict[str, Any], now: float) -> None:
        if name == "tool_call":
            args = payload.get("args")
            turn.tool_calls.append(
                ToolCall(
                    name=str(payload.get("name") or "?"),
                    args=args if isinstance(args, dict) else {},
                    at=now,
                )
            )
            if turn.first_tool_s is None:
                turn.first_tool_s = now
        elif name == "tool_result":
            tname = str(payload.get("name") or "")
            ok = bool(payload.get("ok", True))
            preview = str(payload.get("preview") or "")
            # Attach to the most recent matching call still awaiting a result.
            for call in reversed(turn.tool_calls):
                if call.ok is None and (not tname or call.name == tname):
                    call.ok = ok
                    call.preview = preview
                    break
            else:
                turn.tool_calls.append(
                    ToolCall(name=tname or "?", ok=ok, preview=preview, at=now)
                )
        elif name == "token":
            turn.text += str(payload.get("text") or payload.get("content") or "")
        elif name == "usage":
            u = payload.get("usage")
            turn.usage = u if isinstance(u, dict) else {
                k: v for k, v in payload.items() if k != "type"
            }
        elif name == "done":
            turn.status = str(payload.get("status") or "ok")
            u = payload.get("usage")
            if isinstance(u, dict):
                turn.usage = u
        elif name == "aborted":
            turn.status = "aborted"
        elif name == "error":
            turn.status = "error"
            turn.error = str(payload.get("message") or "stream error")


def _iter_sse(resp: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event_name, payload)`` from a text/event-stream response."""
    event = "message"
    data_lines: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith(":"):  # keepalive comment
            continue
        if line == "":
            if data_lines:
                blob = "\n".join(data_lines)
                data_lines = []
                try:
                    payload = json.loads(blob)
                except json.JSONDecodeError:
                    payload = {"raw": blob}
                yield event, payload if isinstance(payload, dict) else {"raw": payload}
            event = "message"
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {"raw": "\n".join(data_lines)}
        yield event, payload if isinstance(payload, dict) else {"raw": payload}

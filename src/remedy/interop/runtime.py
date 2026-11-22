"""Facade used by A2A, MCP, HTTP, and the peer.* worker tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from remedy import __version__
from remedy.core.organism_knobs import resolve, resolve_bool, resolve_int
from remedy.interop import audit, consent, peers, tasks as taskstore
from remedy.interop.a2a import extract_text, text_message
from remedy.interop.card import extended_card, public_card
from remedy.interop.identity import TrustResult, verifier_for
from remedy.interop.language import for_peer, inbound_plain
from remedy.interop.mcp_talk import TALK_TOOLS, call_talk_tool, negotiate_version
from remedy.interop.orchestrate import delegate_to_peer, merge_results, pick_peers
from remedy.interop.paths import inbox_path, resolve_home
from remedy.interop.token import resolve_token, token_matches

Responder = Callable[[str, dict[str, Any]], str]


# Owner-launched `remedy mcp serve` (stdio) is a local client, not a stranger.
LOCAL_MCP_PEER = {
    "id": "local-mcp",
    "name": "Local MCP client",
    "protocol": "mcp",
    "enabled": True,
    "permissions": ["ask", "delegate", "task_status", "share_context"],
}


class InteropRuntime:
    def __init__(
        self,
        home: str | None,
        *,
        cfg: dict[str, Any] | None = None,
        responder: Responder | None = None,
        verifier: Any | None = None,
        public_url: str | None = None,
        listen_host: str | None = None,
        inbound_enabled: bool | None = None,
    ) -> None:
        self.home = str(resolve_home(home))
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.responder = responder
        self._verifier_override = verifier
        self.public_url = public_url or "http://127.0.0.1:7400/a2a"
        self._listen_host = listen_host
        self._inbound_override = inbound_enabled
        self._mcp = None  # lazy RemedyMCPServer-compatible handle

    # --- knobs -------------------------------------------------------------

    def inbound_enabled(self) -> bool:
        if self._inbound_override is not None:
            return bool(self._inbound_override)
        return resolve_bool("interop_inbound", self.cfg)

    def bind_host(self) -> str:
        if self._listen_host:
            return self._listen_host
        value = str(resolve("interop_bind", self.cfg) or "127.0.0.1").strip().lower()
        aliases = {"localhost": "127.0.0.1", "loopback": "127.0.0.1", "off": "127.0.0.1"}
        return aliases.get(value, value)

    def bind_is_loopback(self) -> bool:
        return self.bind_host() in {"127.0.0.1", "localhost", "::1", "loopback"}

    def a2a_enabled(self) -> bool:
        return resolve_bool("interop_a2a", self.cfg)

    def mcp_enabled(self) -> bool:
        return resolve_bool("interop_mcp", self.cfg)

    def auto_reply(self) -> bool:
        return resolve_bool("interop_auto_reply", self.cfg)

    def require_token(self) -> bool:
        # Inbound HTTP always requires a token once inbound is on.
        return True

    # --- HTTP gate ---------------------------------------------------------

    def authorize_http(self, method: str, path: str, headers: dict[str, str]) -> tuple[bool, str, int]:
        if not self.inbound_enabled():
            return False, "inbound interop is off", 404
        path_l = path.split("?", 1)[0]
        is_a2a = path_l.startswith("/a2a") or path_l.startswith("/.well-known/") or path_l.endswith("extendedAgentCard")
        is_mcp = path_l.startswith("/mcp")
        if is_a2a and not self.a2a_enabled():
            return False, "A2A is disabled", 404
        if is_mcp and not self.mcp_enabled():
            return False, "MCP is disabled", 404
        # Public agent card is served on loopback without a token so a peer
        # can discover securitySchemes. Non-loopback or extended card needs auth.
        is_public_card = path_l in {"/.well-known/agent-card.json", "/.well-known/agent.json"} and method == "GET"
        if is_public_card and self.bind_is_loopback():
            return True, "ok", 200
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if not token_matches(auth, self.home):
            return False, "missing or invalid bearer token", 401
        return True, "ok", 200

    def remote_ok(self, remote_addr: str | None) -> bool:
        if self.bind_is_loopback():
            host = (remote_addr or "").split("%")[0]
            if host.startswith("[") and "]" in host:
                host = host[1 : host.index("]")]
            if ":" in host and host.count(":") == 1:
                host = host.split(":")[0]
            return host in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", ""}
        return True

    # --- identity / peers --------------------------------------------------

    def verifier(self):
        return verifier_for(self.cfg, self._verifier_override)

    def trust_for(self, peer: dict[str, Any] | None, presented: dict[str, Any] | None = None) -> TrustResult:
        info = dict(peer or {})
        if presented:
            if presented.get("did") and not info.get("did"):
                info["did"] = presented["did"]
        return self.verifier().verify(info)

    def load_peer(self, peer_id: str | None) -> dict[str, Any] | None:
        if not peer_id:
            return None
        return peers.get(peers.load(self.home), peer_id)

    def upsert_peer(self, peer: dict[str, Any]) -> dict[str, Any]:
        return peers.upsert(peers.load(self.home), peer, home=self.home, cfg=self.cfg)

    def list_contacts(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        return peers.list_peers(peers.load(self.home), enabled_only=enabled_only)

    # --- cards -------------------------------------------------------------

    def public_card(self) -> dict[str, Any]:
        return public_card(url=self.public_url, version=__version__)

    def extended_card(self) -> dict[str, Any]:
        return extended_card(self.public_card())

    # --- consent + guards --------------------------------------------------

    def gate(self, action: str, *, peer_id: str | None, metadata: dict[str, Any] | None, text: str, source: str = "http") -> consent.Decision:
        hop_err = consent.check_hop(metadata, self.cfg)
        if hop_err:
            return consent.Decision(action, "deny", hop_err, "untrusted")
        rate_id = peer_id or ("local-mcp" if source == "stdio" else "_unknown")
        rate_err = consent.check_rate(self.home, rate_id, self.cfg)
        if rate_err:
            return consent.Decision(action, "deny", rate_err, "untrusted")
        loop_err = consent.check_loop(self.home, consent.loop_key(rate_id, text, (metadata or {}).get("contextId")))
        if loop_err:
            return consent.Decision(action, "deny", loop_err, "untrusted")
        if source == "stdio" and not peer_id:
            peer = dict(LOCAL_MCP_PEER)
            trust = TrustResult("verified", None, "owner-launched local MCP", "local")
            return consent.decide(action, peer=peer, trust=trust, policy=consent.trust_policy(self.cfg))
        peer = self.load_peer(peer_id)
        trust = self.trust_for(peer, metadata)
        return consent.decide(action, peer=peer, trust=trust, policy=consent.trust_policy(self.cfg))

    # --- inbox / audit -----------------------------------------------------

    def _inbox(self, entry: dict[str, Any]) -> None:
        path = inbox_path(self.home)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _audit(self, direction: str, protocol: str, peer_id: str | None, action: str, summary: str, extra: dict[str, Any] | None = None) -> None:
        audit.record(
            self.home,
            direction=direction,
            protocol=protocol,
            peer_id=peer_id,
            action=action,
            summary=summary,
            extra=extra,
            cfg=self.cfg,
        )

    # --- ask / delegate / share / status -----------------------------------

    def ask(self, question: str, *, peer_id: str | None = None, metadata: dict[str, Any] | None = None, source: str = "http") -> dict[str, Any]:
        text = inbound_plain(question)
        decision = self.gate("ask", peer_id=peer_id, metadata=metadata, text=text, source=source)
        return self._run("ask", text, decision, peer_id=peer_id, metadata=metadata)

    def delegate(self, task: str, *, peer_id: str | None = None, context: str | None = None, metadata: dict[str, Any] | None = None, source: str = "http") -> dict[str, Any]:
        text = inbound_plain(task)
        if context:
            text = text + "\n\nContext:\n" + inbound_plain(context)
        decision = self.gate("delegate", peer_id=peer_id, metadata=metadata, text=text, source=source)
        return self._run("delegate", text, decision, peer_id=peer_id, metadata=metadata)

    def share_context(self, context: str, *, peer_id: str | None = None, task_id: str | None = None, metadata: dict[str, Any] | None = None, source: str = "http") -> dict[str, Any]:
        text = inbound_plain(context)
        decision = self.gate("share_context", peer_id=peer_id, metadata=metadata, text=text, source=source)
        if decision.outcome == "deny":
            self._audit("in", "mcp", peer_id, "share_context", text, {"reason": decision.reason})
            return {"ok": False, "error": decision.reason, "decision": decision.outcome}
        store = taskstore.load(self.home)
        if task_id:
            task = taskstore.get(store, task_id)
            if task is None:
                return {"ok": False, "error": "unknown task"}
            msg = text_message(text, role="user", context_id=task.get("contextId"))
            taskstore.update_state(store, task_id, (task.get("status") or {}).get("state") or taskstore.WORKING, home=self.home, message=msg)
            self._audit("in", "mcp", peer_id, "share_context", text, {"task_id": task_id})
            return {"ok": True, "task_id": task_id, "queued": True}
        task = self._new_task("share_context", text, peer_id, decision, metadata=metadata)
        return {"ok": True, "task_id": task["id"], "state": (task.get("status") or {}).get("state"), "queued": True}

    def task_status(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            return {"ok": False, "error": "unknown task"}
        self._audit("in", "mcp", None, "task_status", task_id)
        return {"ok": True, "task": task}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return taskstore.get(taskstore.load(self.home), task_id)

    def list_tasks(self, **kwargs: Any) -> tuple[list[dict[str, Any]], str | None]:
        return taskstore.list_tasks(taskstore.load(self.home), **kwargs)

    def cancel_task(self, task_id: str) -> tuple[dict[str, Any] | None, str | None]:
        store = taskstore.load(self.home)
        return taskstore.cancel(store, task_id, home=self.home)

    def _new_task(
        self,
        action: str,
        text: str,
        peer_id: str | None,
        decision: consent.Decision,
        *,
        metadata: dict[str, Any] | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        if state is None:
            if decision.outcome == "need_approval":
                state = taskstore.INPUT_REQUIRED
            elif decision.outcome == "deny":
                state = taskstore.REJECTED
            else:
                state = taskstore.SUBMITTED
        msg = text_message(text, role="user")
        store = taskstore.load(self.home)
        task = taskstore.create(
            store,
            home=self.home,
            message=msg,
            context_id=(metadata or {}).get("contextId") if metadata else None,
            peer_id=peer_id,
            action=action,
            state=state,
            metadata={
                "decision": decision.outcome,
                "reason": decision.reason,
                "trust": decision.trust,
                "hop": consent.hop_count(metadata),
                **({k: v for k, v in (metadata or {}).items() if k not in {"authorization", "token", "api_key"}}),
            },
            cfg=self.cfg,
        )
        self._inbox({
            "ts": task["metadata"]["created_ts"],
            "task_id": task["id"],
            "peer_id": peer_id,
            "action": action,
            "decision": decision.outcome,
            "text": text[:2000],
        })
        return task

    def _run(
        self,
        action: str,
        text: str,
        decision: consent.Decision,
        *,
        peer_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        protocol = "mcp"
        if decision.outcome == "deny":
            task = self._new_task(action, text, peer_id, decision, metadata=metadata, state=taskstore.REJECTED)
            self._audit("in", protocol, peer_id, action, text, {"reason": decision.reason, "task_id": task["id"]})
            return {
                "ok": False,
                "error": decision.reason,
                "decision": decision.outcome,
                "task_id": task["id"],
                "state": taskstore.REJECTED,
            }
        if decision.outcome == "need_approval" or not self.auto_reply() or self.responder is None:
            state = taskstore.INPUT_REQUIRED if decision.outcome == "need_approval" else taskstore.SUBMITTED
            task = self._new_task(action, text, peer_id, decision, metadata=metadata, state=state)
            english, _ = for_peer(
                "Queued for the owner." if decision.outcome == "need_approval"
                else "Received and queued. Automatic replies are off."
            )
            self._audit("in", protocol, peer_id, action, text, {"task_id": task["id"], "decision": decision.outcome})
            return {
                "ok": True,
                "queued": True,
                "needs_approval": decision.outcome == "need_approval",
                "reason": decision.reason,
                "task_id": task["id"],
                "state": state,
                "text": english,
            }
        # auto-reply via injectable responder (tests, or a future session hook)
        task = self._new_task(action, text, peer_id, decision, metadata=metadata, state=taskstore.WORKING)
        try:
            reply = self.responder(text, {"action": action, "peer_id": peer_id, "task_id": task["id"]})
        except Exception as exc:
            store = taskstore.load(self.home)
            taskstore.update_state(store, task["id"], taskstore.FAILED, home=self.home, message=text_message(str(exc)))
            self._audit("out", protocol, peer_id, action, str(exc), {"task_id": task["id"], "failed": True})
            return {"ok": False, "error": "responder failed", "task_id": task["id"], "state": taskstore.FAILED}
        english, structured = for_peer(str(reply), home=self.home)
        artifact = {
            "artifactId": str(uuid.uuid4()),
            "parts": ([{"text": english}] + ([{"data": structured}] if structured else [])),
        }
        store = taskstore.load(self.home)
        updated = taskstore.update_state(
            store,
            task["id"],
            taskstore.COMPLETED,
            home=self.home,
            message=text_message(english, context_id=task.get("contextId")),
            artifact=artifact,
        )
        self._audit("out", protocol, peer_id, action, english, {"task_id": task["id"]})
        return {
            "ok": True,
            "queued": False,
            "task_id": task["id"],
            "state": taskstore.COMPLETED,
            "text": english,
            "data": structured,
            "task": updated,
        }

    # --- A2A ---------------------------------------------------------------

    def a2a_send(self, params: dict[str, Any], *, stream: bool = False, headers: dict[str, str] | None = None) -> Any:
        message = params.get("message") if isinstance(params.get("message"), dict) else params
        text = extract_text(message) if isinstance(message, dict) else inbound_plain(str(params.get("text") or ""))
        metadata = {}
        if isinstance(message, dict) and isinstance(message.get("metadata"), dict):
            metadata.update(message["metadata"])
        if isinstance(params.get("metadata"), dict):
            metadata.update(params["metadata"])
        peer_id = str(metadata.get("peer_id") or metadata.get("from") or "").strip() or None
        action = str(metadata.get("action") or "ask").strip() or "ask"
        if action not in {"ask", "delegate", "share_context", "task_status"}:
            action = "ask"
        config = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
        result = self.ask(text, peer_id=peer_id, metadata=metadata) if action == "ask" else (
            self.delegate(text, peer_id=peer_id, metadata=metadata) if action == "delegate" else self.share_context(text, peer_id=peer_id, metadata=metadata)
        )
        task_id = result.get("task_id")
        task = self.get_task(task_id) if task_id else None
        # Blocking default: wait only if we already completed (no live mind
        # in this process). return_immediately just returns the task as-is.
        if task is None:
            # Direct Message response for a simple denial with no store write? We always write a task.
            raise ValueError("failed to record task")
        self._audit("in", "a2a", peer_id, action, text, {"task_id": task_id, "stream": stream})
        _ = config  # reserved for future push-notification / blocking wait
        return {"task": task}

    # --- MCP JSON-RPC (talk tools + initialize) ----------------------------

    def handle_mcp(self, message: dict[str, Any], *, source: str = "http") -> dict[str, Any] | None:
        mid = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if mid is None and method:
            return None
        if method == "initialize":
            version = negotiate_version(str(params.get("protocolVersion") or "") or None)
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "remedy", "version": __version__},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            tools = list(TALK_TOOLS)
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                text = call_talk_tool(self, name, arguments, source=source)
                return {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }
            except KeyError:
                return {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
                }
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"resources": []}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    # --- outbound orchestration (for peer.* tools) -------------------------

    def orchestrate(self, query: str, *, limit: int = 3, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        english, _ = for_peer(query, home=self.home)
        # Outbound spend/send/delete still need the owner — the tool caller
        # (Remedy) already went through PolicyEngine. We still refuse to
        # forward an outbound that looks like those acts without approval.
        hop = consent.hop_count(metadata)
        if hop >= resolve_int("interop_hop_limit", self.cfg):
            return {"ok": False, "error": "hop limit exceeded", "results": [], "summary": ""}
        chosen = pick_peers(english, home=self.home, limit=limit)
        if not chosen:
            return {"ok": False, "error": "no matching peer", "results": [], "summary": ""}
        results = []
        for peer in chosen:
            # Per-peer outbound is not a grant to spend/send/delete.
            results.append(delegate_to_peer(peer, english, metadata=metadata, hop=hop))
            self._audit("out", "a2a", str(peer.get("id")), "delegate", english, {"endpoint": peer.get("endpoint")})
        merged = merge_results(results)
        return {"ok": True, **merged}

    def ensure_inbound_token(self) -> str:
        return resolve_token(self.home, create=True)

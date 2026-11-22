"""Peer-agent interop: A2A, MCP talk-to-Remedy, consent, and contacts."""

from __future__ import annotations

import json
import threading

import pytest

from remedy.core import organism_knobs as knobs
from remedy.interop import consent, peers, tasks as taskstore
from remedy.interop.a2a import A2AClient, A2AHandler, extract_text
from remedy.interop.card import WELL_KNOWN_PATH, public_card
from remedy.interop.http import handle_request
from remedy.interop.identity import ClaimidxVerifier, NullVerifier, TrustResult
from remedy.interop.language import for_peer
from remedy.interop.mcp_talk import TALK_TOOLS, call_talk_tool
from remedy.interop.orchestrate import merge_results, pick_peers, score_peer
from remedy.interop.redact import redact, redact_text
from remedy.interop.runtime import InteropRuntime
from remedy.interop.token import resolve_token, write_token
from remedy.tools.mcp_server import RemedyMCPServer


def _rt(tmp_path, **kwargs) -> InteropRuntime:
    cfg = kwargs.pop("cfg", {"advanced": {"interop_inbound": True, "interop_audit": True}})
    return InteropRuntime(str(tmp_path), cfg=cfg, inbound_enabled=True, **kwargs)


def test_knob_defaults_are_safe() -> None:
    table = knobs.effective_table({})
    assert table["interop_inbound"] is False
    assert table["interop_bind"] == "127.0.0.1"
    assert table["interop_a2a"] is True
    assert table["interop_mcp"] is True
    assert table["interop_claimidx"] is False
    assert table["interop_trust_policy"] == "gated"
    assert table["interop_auto_reply"] is True
    assert table["interop_audit"] is True
    assert table["interop_rate_per_min"] == 30
    assert table["interop_hop_limit"] == 3
    assert table["interop_max_concurrent"] == 2
    keys = {item["key"] for item in knobs.schema()}
    assert "interop_inbound" in keys
    assert "interop_max_concurrent" in keys


def test_agent_card_is_a2a_v1(tmp_path) -> None:
    card = public_card(url="http://127.0.0.1:7400/a2a")
    assert card["name"] == "Remedy"
    iface = card["supportedInterfaces"][0]
    assert iface["protocolBinding"] == "JSONRPC"
    assert iface["protocolVersion"] == "1.0"
    assert iface["url"].endswith("/a2a")
    assert "bearer" in card["securitySchemes"]
    ids = {s["id"] for s in card["skills"]}
    assert {"ask", "delegate", "share_context"} <= ids


def test_a2a_send_and_get_task(tmp_path) -> None:
    peer = {
        "id": "claude",
        "name": "Claude",
        "protocol": "mcp",
        "endpoint": "stdio",
        "permissions": ["ask", "delegate", "task_status", "share_context"],
        "enabled": True,
    }
    rt = _rt(tmp_path)
    rt.upsert_peer(peer)
    handler = A2AHandler(rt)
    resp = handler.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"text": "What is on the list?"}],
                    "metadata": {"peer_id": "claude", "action": "ask"},
                }
            },
        },
        headers={"a2a-version": "1.0"},
    )
    task = resp["result"]["task"]
    assert task["id"]
    assert task["status"]["state"] in {
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    }
    # v0.3 alias
    got = handler.handle({"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task["id"]}})
    assert got["result"]["id"] == task["id"]
    # unknown method
    err = handler.handle({"jsonrpc": "2.0", "id": 3, "method": "nope", "params": {}})
    assert err["error"]["code"] == -32601
    # unsupported version
    verr = handler.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "GetTask", "params": {"id": task["id"]}},
        headers={"a2a-version": "2.0"},
    )
    assert verr["error"]["code"] == -32009


def test_consent_always_requires_owner_for_money_send_delete() -> None:
    peer = {
        "id": "p",
        "enabled": True,
        "permissions": ["ask", "spend", "send", "delete"],
    }
    trust = TrustResult("verified", "did:claimidx:friend", "ok", "claimidx")
    for action in ("spend", "send", "delete", "transact", "communicate"):
        d = consent.decide(action, peer=peer, trust=trust, policy="open")
        assert d.outcome == "need_approval", action


def test_trust_policy_verified_denies_unverified() -> None:
    peer = {"id": "p", "enabled": True, "permissions": ["ask", "delegate"]}
    trust = TrustResult("unverified", None, "no did", "none")
    d = consent.decide("ask", peer=peer, trust=trust, policy="verified")
    assert d.outcome == "deny"


def test_gated_unknown_peer_queues_asks(tmp_path) -> None:
    rt = _rt(tmp_path, cfg={"advanced": {"interop_trust_policy": "gated", "interop_inbound": True}})
    out = rt.ask("Hello?", source="http")
    assert out["ok"] is True
    assert out.get("needs_approval") is True
    assert out["state"] == "TASK_STATE_INPUT_REQUIRED"


def test_hop_and_loop_guards(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.upsert_peer(
        {
            "id": "p",
            "protocol": "a2a",
            "endpoint": "http://127.0.0.1:9/a2a",
            "permissions": ["ask"],
            "enabled": True,
        }
    )
    denied = rt.ask("ping", peer_id="p", metadata={"hop": 99})
    assert denied["ok"] is False
    assert "hop" in denied["error"]

    # Same payload three times trips the ping-pong guard.
    rt2 = _rt(tmp_path)
    # reuse same home so the loop window accumulates
    results = [rt2.ask("same payload", peer_id="p") for _ in range(4)]
    assert any(r.get("ok") is False and "ping-pong" in str(r.get("error") or "") for r in results)


def test_language_strips_ril1() -> None:
    english, _ = for_peer("ril1 note L=1 v=dense hello-there")
    assert "ril1" not in english.lower() or english != "ril1 note L=1 v=dense hello-there"
    # Plain English is unchanged.
    text, data = for_peer("Hello from a peer.")
    assert text == "Hello from a peer."
    assert data is None


def test_redact_does_not_log_tokens() -> None:
    assert "***" in redact_text("Authorization: Bearer supersecretvalue")
    out = redact({"authorization": "Bearer abc", "text": "hi"})
    assert out["authorization"] == "***"
    assert out["text"] == "hi"


def test_peers_never_persist_secrets(tmp_path) -> None:
    store = peers.empty_store()
    rec = peers.upsert(
        store,
        {
            "id": "claude",
            "name": "Claude",
            "protocol": "mcp",
            "endpoint": "stdio",
            "api_key": "should-not-be-saved",
            "token": "nope",
            "permissions": ["ask"],
        },
        home=str(tmp_path),
    )
    assert "api_key" not in rec
    raw = (tmp_path / "interop" / "peers.json").read_text(encoding="utf-8")
    assert "should-not-be-saved" not in raw
    assert "nope" not in raw
    with pytest.raises(ValueError):
        peers.upsert(store, {"id": "x", "protocol": "proprietary"}, home=str(tmp_path))


def test_stdio_mcp_talk_tools_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = _rt(tmp_path, responder=lambda text, meta: f"You asked: {text}")
    # inbound is on (via _rt) and auto-reply defaults on, so stdio gets a live answer
    out = json.loads(call_talk_tool(rt, "remedy_ask", {"question": "How are you?"}, source="stdio"))
    assert out["ok"] is True
    assert out["task_id"]
    assert out.get("queued") is False
    assert "How are you?" in (out.get("text") or "")
    status = json.loads(call_talk_tool(rt, "remedy_task_status", {"task_id": out["task_id"]}, source="stdio"))
    assert status["ok"] is True
    names = {t["name"] for t in TALK_TOOLS}
    assert names == {"remedy_ask", "remedy_delegate", "remedy_task_status", "remedy_share_context"}


def test_mcp_server_lists_talk_tools_and_keeps_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    srv = RemedyMCPServer(runtime=_rt(tmp_path))
    listed = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "remedy_skill_list" in names
    assert "remedy_ask" in names
    init = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t"}},
        }
    )
    assert init["result"]["protocolVersion"] == "2024-11-05"


def test_http_inbound_off_and_auth(tmp_path) -> None:
    off = InteropRuntime(str(tmp_path), inbound_enabled=False)
    resp = handle_request(off, method="GET", path=WELL_KNOWN_PATH, headers={}, body=b"")
    assert resp.status == 404

    token_path = tmp_path / "interop" / "inbound_token"
    write_token(token_path, "test-token-value-for-http")
    on = InteropRuntime(str(tmp_path), inbound_enabled=True, listen_host="127.0.0.1")
    card = handle_request(on, method="GET", path=WELL_KNOWN_PATH, headers={}, body=b"")
    assert card.status == 200
    denied = handle_request(
        on,
        method="POST",
        path="/a2a",
        headers={"Content-Type": "application/json"},
        body=b'{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"parts":[{"text":"hi"}]}}}',
    )
    assert denied.status == 401
    ok = handle_request(
        on,
        method="POST",
        path="/a2a",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-token-value-for-http"},
        body=b'{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"parts":[{"text":"hi"}]}}}',
    )
    assert ok.status == 200
    payload = json.loads(ok.body)
    assert payload["result"]["task"]["id"]


def test_orchestrate_picks_and_merges(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.upsert_peer(
        {
            "id": "writer",
            "name": "Writer",
            "protocol": "a2a",
            "endpoint": "http://127.0.0.1:9/a2a",
            "skills": ["writing", "summarize"],
            "permissions": ["ask"],
            "enabled": True,
        }
    )
    rt.upsert_peer(
        {
            "id": "coder",
            "name": "Coder",
            "protocol": "a2a",
            "endpoint": "http://127.0.0.1:9/a2a",
            "skills": ["code", "review"],
            "permissions": ["ask"],
            "enabled": True,
        }
    )
    picked = pick_peers("please review this code", home=str(tmp_path))
    assert picked and picked[0]["id"] == "coder"
    merged = merge_results(
        [
            {"peer_id": "coder", "text": "Looks good.", "ok": True},
            {"peer_id": "writer", "error": "down"},
        ]
    )
    assert "Looks good" in merged["summary"]
    assert merged["results"][1]["ok"] is False
    # No live peer: orchestrate reports the transport error, not a crash.
    out = rt.orchestrate("review this code")
    assert out["ok"] is True
    assert out["results"]


def test_auto_reply_uses_responder_and_strips_ril1(tmp_path) -> None:
    def reply(text, meta):
        return "ril1 note L=1 v=dense inner-only"

    rt = _rt(
        tmp_path,
        responder=reply,
        cfg={"advanced": {"interop_auto_reply": True, "interop_inbound": True, "interop_trust_policy": "open"}},
    )
    out = rt.ask("Hi there", source="stdio")
    assert out["ok"] is True
    assert out["state"] == "TASK_STATE_COMPLETED"
    assert "ril1" not in (out.get("text") or "").lower()


def test_identity_is_pluggable_and_claimidx_is_optional() -> None:
    null = NullVerifier()
    assert null.verify({"did": "did:claimidx:friend"}).level == "unverified"
    assert null.verify({"did": "not-a-did"}).level in {"unverified", "untrusted"}
    # Malformed claimidx DID is untrusted even if the verifier is on.
    bad = ClaimidxVerifier().verify({"did": "did:claimidx:!!!"})
    assert bad.level == "untrusted"
    # Well-formed, host down → unverified, never required.
    ok = ClaimidxVerifier().verify({"did": "did:claimidx:friend"})
    assert ok.level in {"unverified", "verified"}


def test_audit_log_written(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.upsert_peer({"id": "p", "protocol": "mcp", "endpoint": "stdio", "permissions": ["ask"], "enabled": True})
    rt.ask("hello audit", peer_id="p", source="http")
    lines = (tmp_path / "interop" / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines
    row = json.loads(lines[-1])
    assert row["action"] == "ask"
    assert row["peer_id"] == "p"


def test_inbound_token_env_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_INTEROP_TOKEN", "from-env")
    assert resolve_token(tmp_path, create=False) == "from-env"


def test_a2a_client_discover_and_send_with_fake_transport() -> None:
    calls: list[tuple[str, bytes | None]] = []

    def opener(url, data, headers):
        calls.append((url, data))
        if data is None:
            return json.dumps(public_card(url="http://peer.example/a2a")).encode()
        body = json.loads(data)
        assert body["method"] == "SendMessage"
        assert headers.get("A2A-Version") == "1.0"
        assert "Authorization" not in headers or "***" not in headers["Authorization"]
        text = extract_text(body["params"]["message"])
        assert "ril1" not in text.lower()
        return json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"message": {"parts": [{"text": "hi"}]}}}).encode()

    client = A2AClient(opener=opener)
    card = client.discover("http://peer.example")
    assert card["name"] == "Remedy"
    result = client.send_message("http://peer.example/a2a", "hello")
    assert result["message"]["parts"][0]["text"] == "hi"
    assert calls


def test_score_peer_empty_skills() -> None:
    assert score_peer({"skills": []}, "code") == 0.0

def test_auto_reply_inert_when_inbound_is_off(tmp_path) -> None:
    seen = []

    def responder(text, meta):
        seen.append(text)
        return "should not run"

    rt = InteropRuntime(
        str(tmp_path),
        cfg={"advanced": {"interop_auto_reply": True, "interop_inbound": False, "interop_trust_policy": "open"}},
        inbound_enabled=False,
        responder=responder,
    )
    out = rt.ask("Hello?", peer_id="claude", metadata={"peer_id": "claude"})
    assert out["queued"] is True
    assert out["state"] == taskstore.SUBMITTED
    assert seen == []


def test_auto_reply_still_queues_spend_send_delete(tmp_path) -> None:
    rt = _rt(
        tmp_path,
        responder=lambda text, meta: "should not run",
        cfg={"advanced": {"interop_auto_reply": True, "interop_inbound": True, "interop_trust_policy": "open"}},
    )
    peers.upsert(
        peers.load(str(tmp_path)),
        {"id": "claude", "protocol": "a2a", "enabled": True, "permissions": ["ask", "spend", "send", "delete"]},
        home=str(tmp_path),
    )
    for action, fn in (
        ("spend", lambda: rt._run("spend", "pay the invoice", rt.gate("spend", peer_id="claude", metadata={}, text="pay the invoice", source="http"), peer_id="claude", metadata={})),
        ("send", lambda: rt._run("send", "email them", rt.gate("send", peer_id="claude", metadata={}, text="email them", source="http"), peer_id="claude", metadata={})),
        ("delete", lambda: rt._run("delete", "remove the file", rt.gate("delete", peer_id="claude", metadata={}, text="remove the file", source="http"), peer_id="claude", metadata={})),
    ):
        out = fn()
        assert out["queued"] is True, action
        assert out["needs_approval"] is True, action
        assert out["state"] == taskstore.INPUT_REQUIRED, action


def test_live_reply_times_out(tmp_path) -> None:
    release = threading.Event()

    def slow(text, meta):
        release.wait(timeout=2)
        return f"done:{text}"

    rt = _rt(
        tmp_path,
        responder=slow,
        turn_timeout_s=0.2,
        cfg={
            "advanced": {
                "interop_auto_reply": True,
                "interop_inbound": True,
                "interop_trust_policy": "open",
            }
        },
    )
    out = rt.ask("held", peer_id="p")
    assert out["ok"] is False
    assert out["state"] == taskstore.FAILED
    assert "timed out" in (out.get("error") or "")
    release.set()


def test_live_reply_respects_concurrency(tmp_path) -> None:
    release = threading.Event()
    started = threading.Event()
    in_flight = []

    def slow(text, meta):
        in_flight.append(text)
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test hold expired")
        return f"done:{text}"

    rt = _rt(
        tmp_path,
        responder=slow,
        turn_timeout_s=2,
        cfg={
            "advanced": {
                "interop_auto_reply": True,
                "interop_inbound": True,
                "interop_trust_policy": "open",
                "interop_max_concurrent": 1,
            }
        },
    )
    results = [None, None]

    def run(i, prompt):
        results[i] = rt.ask(prompt, peer_id=f"p{i}")

    t1 = threading.Thread(target=run, args=(0, "one"))
    t1.start()
    assert started.wait(timeout=1)
    t2 = threading.Thread(target=run, args=(1, "two"))
    t2.start()
    t2.join(timeout=0.15)
    # Second still waiting for the single slot; only one responder entered.
    assert in_flight == ["one"]
    assert results[1] is None
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert results[0]["ok"] is True
    assert results[0]["queued"] is False
    assert results[0]["text"].startswith("done:")
    assert results[1]["ok"] is True
    assert results[1]["text"].startswith("done:")


def test_live_reply_is_audited(tmp_path) -> None:
    rt = _rt(
        tmp_path,
        responder=lambda text, meta: f"ack {text}",
        cfg={"advanced": {"interop_auto_reply": True, "interop_inbound": True, "interop_trust_policy": "open", "interop_audit": True}},
    )
    out = rt.ask("ping", peer_id="claude")
    assert out["queued"] is False
    log = (tmp_path / "interop" / "audit.jsonl").read_text(encoding="utf-8")
    assert "ping" in log
    assert '"live": true' in log or '"live":true' in log


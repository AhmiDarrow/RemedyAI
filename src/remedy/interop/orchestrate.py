"""Pick a peer by skills, delegate over A2A or MCP, and merge results."""

from __future__ import annotations

import re
from typing import Any

from remedy.interop.a2a import A2AClient, extract_text
from remedy.interop.language import for_peer
from remedy.interop.peers import list_peers, load as load_peers

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,32}", re.IGNORECASE)


def tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def score_peer(peer: dict[str, Any], query: str) -> float:
    skills = [str(s).lower() for s in (peer.get("skills") or [])]
    if not skills:
        return 0.0
    q = tokenize(query)
    if not q:
        return 0.0
    skill_tokens = set()
    for skill in skills:
        skill_tokens |= tokenize(skill)
        skill_tokens.add(skill)
    overlap = q & skill_tokens
    if not overlap:
        # substring fallback: "code review" vs skill "code"
        hits = sum(1 for s in skills if s and s in query.lower())
        return float(hits) * 0.5
    return float(len(overlap)) + (0.25 if any(s in query.lower() for s in skills) else 0.0)


def pick_peers(
    query: str,
    *,
    home: str | None = None,
    store: dict[str, Any] | None = None,
    limit: int = 3,
    protocol: str | None = None,
) -> list[dict[str, Any]]:
    store = store if store is not None else load_peers(home)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for peer in list_peers(store, enabled_only=True):
        if protocol and str(peer.get("protocol") or "") != protocol:
            continue
        ranked.append((score_peer(peer, query), peer))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
    return [peer for score, peer in ranked if score > 0][: max(1, limit)]


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine peer replies into one structured result plus a plain summary."""
    lines: list[str] = []
    parts: list[dict[str, Any]] = []
    for item in results:
        peer_id = str(item.get("peer_id") or item.get("name") or "peer")
        text = str(item.get("text") or "").strip()
        error = item.get("error")
        if error:
            lines.append(f"{peer_id}: error — {error}")
            parts.append({"peer_id": peer_id, "ok": False, "error": str(error)})
            continue
        english, structured = for_peer(text)
        lines.append(f"{peer_id}: {english}" if english else f"{peer_id}: (empty)")
        entry: dict[str, Any] = {"peer_id": peer_id, "ok": True, "text": english}
        if structured:
            entry["data"] = structured
        if item.get("task_id"):
            entry["task_id"] = item["task_id"]
        parts.append(entry)
    return {"summary": "\n".join(lines).strip(), "results": parts}


def delegate_to_peer(
    peer: dict[str, Any],
    text: str,
    *,
    client: A2AClient | None = None,
    token: str | None = None,
    metadata: dict[str, Any] | None = None,
    hop: int = 0,
) -> dict[str, Any]:
    """Send a task to one peer over A2A. MCP peers are contacted at their endpoint the same way when they speak A2A; otherwise the caller uses MCP tools."""
    protocol = str(peer.get("protocol") or "a2a").lower()
    endpoint = str(peer.get("endpoint") or "").strip()
    peer_id = str(peer.get("id") or "")
    if not endpoint:
        return {"peer_id": peer_id, "ok": False, "error": "peer has no endpoint"}
    meta = {"hop": hop + 1, "from": "remedy", **(metadata or {})}
    if protocol == "mcp":
        # Outbound MCP to a peer is "call their talk tools" — not implemented
        # as a vendor adapter. If the peer also publishes A2A, use that.
        # Callers may pass a custom client. Default: try A2A at the endpoint.
        pass
    client = client or A2AClient()
    try:
        result = client.send_message(endpoint, text, token=token, metadata=meta)
    except Exception as exc:
        return {"peer_id": peer_id, "ok": False, "error": str(exc)}
    text_out = ""
    task_id = None
    if isinstance(result, dict):
        if "task" in result and isinstance(result["task"], dict):
            task = result["task"]
            task_id = task.get("id")
            text_out = extract_text((task.get("status") or {}).get("message"))
        elif "message" in result and isinstance(result["message"], dict):
            text_out = extract_text(result["message"])
        elif result.get("id") and result.get("status"):
            task_id = result.get("id")
            text_out = extract_text((result.get("status") or {}).get("message"))
        else:
            text_out = extract_text(result) or str(result.get("text") or "")
    return {"peer_id": peer_id, "ok": True, "text": text_out, "task_id": task_id, "raw": result}

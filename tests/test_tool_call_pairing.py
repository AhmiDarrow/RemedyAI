"""Tool-call / tool-result pairing contract (OpenAI-compatible HTTP 400 guard)."""

from __future__ import annotations

from remedy.core.react_stream import ensure_tool_call_pairings, normalize_tool_calls


def test_normalize_tool_calls_assigns_ids_and_skips_empty_names():
    raw = [
        {
            "id": "",
            "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"},
        },
        {
            "id": "keep_me",
            "type": "function",
            "function": {"name": "file_read", "arguments": {"path": "a.py"}},
        },
        {
            "function": {"name": "", "arguments": "{}"},
        },
    ]
    out = normalize_tool_calls(raw)
    assert len(out) == 2
    assert out[0]["id"]
    assert out[0]["function"]["name"] == "list_dir"
    assert out[1]["id"] == "keep_me"
    assert out[1]["function"]["arguments"] == '{"path": "a.py"}'

def test_ensure_tool_call_pairings_fills_missing_results():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "review project"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": '{"path":"x"}'},
                },
            ],
        },
        # Only one result — the classic incomplete-pairing bug.
        {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
        {"role": "user", "content": "continue"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    tool_ids = [
        m["tool_call_id"] for m in fixed if m.get("role") == "tool"
    ]
    assert tool_ids == ["call_a", "call_b"]
    assert any("missing tool result" in (m.get("content") or "") for m in fixed if m.get("tool_call_id") == "call_b")
    # User message after tools is preserved.
    assert fixed[-1]["role"] == "user"

def test_ensure_tool_call_pairings_drops_orphan_tool_messages():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "nope"},
        {"role": "assistant", "content": "ok"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    assert [m["role"] for m in fixed] == ["user", "assistant"]

def test_ensure_tool_call_pairings_multistep_looks_ahead_past_injects():
    """Epoch / re-arm user injects must not orphan later tool results (HTTP 400).

    Multi-step layout that broke when pairing only scanned consecutive tools:
      assistant [a,b] → tool a → user(epoch) → tool b → assistant [c] → tool c
    """
    messages = [
        {"role": "user", "content": "implement the login fix"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path":"app.py"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "files…"},
        # Soft epoch / re-arm inject lands before the second tool result.
        {
            "role": "user",
            "content": (
                "[Epoch 1 complete at step 256] Context was compacted — "
                "this is not a stop. Run until the task is finished."
            ),
        },
        {"role": "tool", "tool_call_id": "call_b", "content": "def login():…"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_c",
                    "type": "function",
                    "function": {
                        "name": "file_edit",
                        "arguments": '{"path":"app.py"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_c", "content": "ok edited"},
        {"role": "user", "content": "continue"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    # All three real tool results preserved (no empty missing stub for call_b).
    tool_by_id = {
        m["tool_call_id"]: m["content"]
        for m in fixed
        if m.get("role") == "tool"
    }
    assert tool_by_id["call_a"] == "files…"
    assert tool_by_id["call_b"] == "def login():…"
    assert tool_by_id["call_c"] == "ok edited"
    assert "missing tool result" not in tool_by_id["call_b"]
    # API order: assistant tool_calls immediately followed by its tool results.
    roles = [m.get("role") for m in fixed]
    # First assistant tools block: assistant, tool, tool, then user epoch, …
    a0 = next(i for i, m in enumerate(fixed) if m.get("tool_calls") and
              any(tc.get("id") == "call_a" for tc in (m.get("tool_calls") or [])))
    assert fixed[a0 + 1]["role"] == "tool"
    assert fixed[a0 + 2]["role"] == "tool"
    assert {fixed[a0 + 1]["tool_call_id"], fixed[a0 + 2]["tool_call_id"]} == {
        "call_a",
        "call_b",
    }
    # Epoch user message still present after the paired tools.
    assert any(
        m.get("role") == "user" and "Epoch 1" in (m.get("content") or "")
        for m in fixed
    )
    assert roles[-1] == "user"

def test_ensure_tool_call_pairings_multistep_two_rounds_intact():
    """Two complete tool rounds with a user mid-message stay valid."""
    messages = [
        {"role": "user", "content": "review project"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "r1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "r1", "content": "src/"},
        {"role": "user", "content": "keep going"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "r2a",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": '{"path":"a"}'},
                },
                {
                    "id": "r2b",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": '{"path":"b"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "r2a", "content": "A"},
        # Missing r2b — synthetic fill still required.
        {"role": "assistant", "content": "partial review"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    tool_ids = [m["tool_call_id"] for m in fixed if m.get("role") == "tool"]
    assert tool_ids == ["r1", "r2a", "r2b"]
    assert any(
        m.get("tool_call_id") == "r2b" and "missing tool result" in (m.get("content") or "")
        for m in fixed
    )

def test_ensure_tool_call_pairings_lookahead_across_epoch_inject():
    """Epoch/re-arm user inject between tool results must not orphan a result."""
    messages = [
        {"role": "user", "content": "review project"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": '{"path":"x"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "dir ok"},
        # Soft-epoch inject landed between results (multi-step thrash).
        {
            "role": "user",
            "content": "Keep going until the user request is finished.",
        },
        {"role": "tool", "tool_call_id": "call_b", "content": "file ok"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_c",
                    "type": "function",
                    "function": {"name": "file_edit", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_c", "content": "edited"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    # call_a + call_b must sit immediately after the first assistant tool_calls
    # (before the epoch inject), so OpenAI pairing stays valid.
    roles = [m.get("role") for m in fixed]
    first_asst = next(
        i for i, m in enumerate(fixed) if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert fixed[first_asst + 1]["role"] == "tool"
    assert fixed[first_asst + 1]["tool_call_id"] == "call_a"
    assert fixed[first_asst + 2]["role"] == "tool"
    assert fixed[first_asst + 2]["tool_call_id"] == "call_b"
    # Epoch inject preserved after the pair, not between tools
    assert fixed[first_asst + 3]["role"] == "user"
    assert "Keep going" in (fixed[first_asst + 3].get("content") or "")
    # Second assistant turn still pairs
    assert "call_c" in {
        m["tool_call_id"] for m in fixed if m.get("role") == "tool"
    }
    assert roles.count("tool") == 3
    # No synthetic missing-result stubs for call_b
    for m in fixed:
        if m.get("tool_call_id") == "call_b":
            assert "missing tool result" not in (m.get("content") or "")

def test_nonstring_tool_call_id_strip_in_pairing() -> None:
    raw = [
        {
            "id": ["call_a"],
            "type": "function",
            "function": {"name": ["list_dir"], "arguments": "{}"},
        }
    ]
    out = normalize_tool_calls(raw)
    assert out[0]["id"] == "call_a"
    assert out[0]["function"]["name"] == "list_dir"
    messages = [
        {"role": "assistant", "content": None, "tool_calls": raw},
        {"role": "tool", "tool_call_id": ["call_a"], "content": "ok"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    ids = [m["tool_call_id"] for m in fixed if m.get("role") == "tool"]
    assert ids == ["call_a"]

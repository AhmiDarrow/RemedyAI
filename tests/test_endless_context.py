"""Fixed n_ctx endless coding — hard-fit cascade under 32k (no bigger model)."""

from __future__ import annotations

from remedy.core.endless_context import (
    CODING_TOOL_PACK,
    estimate_messages_tokens,
    estimate_tools_tokens,
    fit_local_request,
    prompt_budget,
    slim_tools_pack,
    tool_pack_for_window,
)
from remedy.core.providers import LlamaCppProvider, RmbProvider


def _fat_tools(n: int = 80) -> list[dict]:
    tools = []
    for i in range(n):
        name = CODING_TOOL_PACK[i] if i < len(CODING_TOOL_PACK) else f"tool_{i:03d}"
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (
                        f"Tool {name} does many things. " + ("detail " * 40)
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            f"arg{j}": {
                                "type": "string",
                                "description": "x" * 80,
                            }
                            for j in range(12)
                        },
                        "required": ["arg0"],
                    },
                },
            }
        )
    return tools


def _fat_messages() -> list[dict]:
    skill_body = "Skills catalog (name+status only):\n" + "\n".join(
        f"- **skill-{i}** [ready]: " + ("y" * 100) for i in range(30)
    )
    auto = "[Skill auto-suggest] Top match: change-safety\n\n" + ("procedure line\n" * 200)
    history = []
    for i in range(20):
        history.append({"role": "user", "content": f"step {i}: " + ("do work " * 50)})
        history.append(
            {
                "role": "assistant",
                "content": f"working on step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": '{"path":"x.py"}',
                        },
                    }
                ],
            }
        )
        history.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": ("FILE CONTENTS\n" + ("line of code\n" * 400)),
            }
        )
    return (
        [
            {
                "role": "system",
                "content": (
                    "Project workspace: C:/proj\n\n"
                    + skill_body
                    + "\n\n"
                    + auto
                    + "\n\nSelf-configuration: configure Remedy...\n\n"
                    "Owner's manual / F1 Help: call help_list..."
                ),
            },
            {"role": "user", "content": "create a calculator app, present when stable"},
        ]
        + history
    )


def test_prompt_budget_reserves_completion():
    budget, completion = prompt_budget(32768)
    assert completion >= 768
    assert budget + completion < 32768
    assert budget > 20_000  # most of 32k is prompt


def test_fit_local_request_32k_handles_fat_payload():
    """The exact failure class from the export: tools+head+history > n_ctx."""
    msgs = _fat_messages()
    tools = _fat_tools(80)
    before = estimate_messages_tokens(msgs) + estimate_tools_tokens(tools)
    assert before > 8192  # would have died on the old 8k server

    fitted_msgs, fitted_tools, meta = fit_local_request(
        msgs,
        tools,
        window=32768,
        provider="rmb",
        model="Qwen2.5-Coder-7B-Instruct-Q4_K_M",
        coding_bias=True,
    )
    after = estimate_messages_tokens(fitted_msgs) + estimate_tools_tokens(fitted_tools)
    budget, _ = prompt_budget(32768)
    assert after <= budget, f"after={after} budget={budget} levels={meta['levels']}"
    assert meta["fits"] is True
    assert fitted_tools is not None
    # Coding tools preserved
    names = {
        (t.get("function") or {}).get("name")
        for t in fitted_tools
        if isinstance(t, dict)
    }
    assert "file_write" in names or "file_edit" in names
    assert "bash_exec" in names or "file_read" in names
    # User goal still present
    blob = " ".join(
        str(m.get("content") or "") for m in fitted_msgs if m.get("role") == "user"
    )
    assert "calculator" in blob.lower()


def test_fit_local_request_8k_still_fits():
    """Even an 8k host must never send an oversize request."""
    msgs = _fat_messages()
    tools = _fat_tools(60)
    fitted_msgs, fitted_tools, meta = fit_local_request(
        msgs, tools, window=8192, provider="llamacpp", model="qwen2.5:7b"
    )
    after = estimate_messages_tokens(fitted_msgs) + estimate_tools_tokens(fitted_tools)
    budget, _ = prompt_budget(8192)
    assert after <= budget
    assert meta["fits"] is True


def test_slim_coding_pack_prefers_build_tools():
    tools = _fat_tools(40)
    pack = slim_tools_pack(tools, names=CODING_TOOL_PACK, max_tools=12)
    assert pack is not None
    names = [(t.get("function") or {}).get("name") for t in pack]
    assert names[0] in CODING_TOOL_PACK
    assert len(pack) <= 12


def test_tool_pack_scales_with_window():
    assert tool_pack_for_window(4096)[0] < tool_pack_for_window(32768)[0]


def test_rmb_provider_build_body_never_exceeds_window(monkeypatch):
    """Provider path must hard-fit before HTTP."""
    import remedy.core.endless_context as ec

    monkeypatch.setattr(ec, "resolve_local_window", lambda **kw: 8192)

    prov = RmbProvider()
    msgs = _fat_messages()
    tools = _fat_tools(50)
    body = prov.build_body(
        "Qwen2.5-Coder-7B-Instruct-Q4_K_M",
        msgs,
        tools,
        stream=True,
    )
    est = estimate_messages_tokens(body["messages"]) + estimate_tools_tokens(
        body.get("tools")
    )
    budget, completion = prompt_budget(8192)
    # max_tokens counts toward n_ctx on llama-server
    mt = int(body.get("max_tokens") or 0)
    assert est <= budget + 64
    assert est + mt <= 8192 + 64
    body.pop("reasoning_effort", None)


def test_llamacpp_provider_counts_tools_in_budget(monkeypatch):
    import remedy.core.endless_context as ec

    monkeypatch.setattr(ec, "resolve_local_window", lambda **kw: 16384)
    prov = LlamaCppProvider()
    body = prov.build_body(
        "qwen2.5:7b",
        [{"role": "user", "content": "hi"}],
        _fat_tools(30),
        stream=False,
    )
    est = estimate_messages_tokens(body["messages"]) + estimate_tools_tokens(
        body.get("tools")
    )
    budget, _ = prompt_budget(16384)
    assert est <= budget

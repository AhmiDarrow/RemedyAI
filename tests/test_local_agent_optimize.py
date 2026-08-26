"""Local/RMB auto-optimize — tool-first, slim system, forced tool_choice."""

from __future__ import annotations

import pytest

from remedy.core.local_agent_optimize import (
    apply_local_body_optimize,
    force_tool_choice_required,
    is_local_binding,
    looks_like_tutorial_monologue,
    message_wants_implement,
    slim_system_for_local,
)
from remedy.core.react_stream import build_runtime_system_block


@pytest.fixture(autouse=True)
def _rmb_thinking_on(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.local_agent_optimize.local_thinking_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "remedy.core.local_agent_optimize._rmb_reasoning_budget",
        lambda: None,
    )


def test_message_wants_implement_calculator():
    assert message_wants_implement(
        "create a calculator app, Present once you have a working calculator"
    )
    assert not message_wants_implement("hi")
    assert not message_wants_implement("thanks")


def test_force_tool_choice_on_create():
    tools = [{"type": "function", "function": {"name": "file_write"}}]
    assert force_tool_choice_required(
        provider="rmb",
        model="Qwen2.5-Coder-7B-Instruct-Q4_K_M",
        base_url="http://127.0.0.1:8787/v1",
        tools=tools,
        user_message="create a calculator app",
        step_index=0,
    )
    assert not force_tool_choice_required(
        provider="openai",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        tools=tools,
        user_message="create a calculator app",
        step_index=0,
    )


def test_slim_system_includes_local_contract():
    big = "Personhood: " + ("x" * 5000)
    out = slim_system_for_local(
        big,
        "Project workspace: C:/proj",
        provider="rmb",
        model="Qwen2.5-Coder-7B",
        user_message="create a calculator app",
    )
    assert "[Local agent mode" in out
    assert "[Local create" in out
    assert "file_write" in out
    assert "Project workspace" in out
    assert len(out) < len(big) + 2000


def test_slim_system_trivia_is_tiny():
    out = slim_system_for_local(
        "Personhood: " + ("x" * 5000),
        "Project workspace: C:/proj\n" + ("ctx " * 4000),
        provider="rmb",
        model="LFM2.5-2.6B",
        user_message="1 + 1",
    )
    assert "[Local agent mode" not in out
    assert "file_write" not in out
    assert "one short sentence" in out.lower()
    assert len(out) < 400


def test_build_runtime_system_block_local_slims():
    out = build_runtime_system_block(
        system_prompt="You are Remedy.\n\n" + ("policy " * 2000),
        provider="rmb",
        model="Qwen2.5-Coder-7B-Instruct-Q4_K_M",
        base_url="http://127.0.0.1:8787/v1",
        max_steps=256,
        context="Project workspace: C:/tmp",
        user_message="create a calculator app",
    )
    assert "[Local agent mode" in out
    assert "create a calculator" in out.lower() or "Local create" in out


def test_apply_local_body_sets_required_tools():
    body = {
        "messages": [
            {"role": "user", "content": "create a calculator app"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "file_write",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "max_tokens": 3000,
        "temperature": 0.7,
    }
    out = apply_local_body_optimize(
        body,
        provider="rmb",
        model="Qwen2.5-Coder-7B",
        base_url="http://127.0.0.1:8787/v1",
        user_message="create a calculator app",
        step_index=0,
    )
    # Implement turns must force tools — "auto" was accepting tutorial monologues.
    assert out["tool_choice"] == "required"
    assert out.get("stream") is False
    # Full-context local: allow larger write budgets (old hard 1024 was a thrash wall)
    assert int(out["max_tokens"]) >= 1024
    assert int(out["max_tokens"]) <= 32_768
    assert float(out["temperature"]) <= 0.15
    assert (out.get("chat_template_kwargs") or {}).get("enable_thinking") is True
    assert "reasoning_budget" not in out


def test_local_chat_completion_cap_is_not_trivia_256():
    """4k RMB ctx used to collapse no-tools answers to 256 (finish=length)."""
    from remedy.core.local_agent_optimize import local_completion_cap

    cap = local_completion_cap(4096, tools_present=False, force_tools=False)
    assert cap >= 768
    # Trivia still uses the 256 ceiling inside apply_local_body_optimize.


def test_slim_system_knowledge_is_not_tool_first():
    out = slim_system_for_local(
        "Personhood: " + ("x" * 5000),
        "Project workspace: C:/proj\nStay with: Continue wiring MTP",
        provider="rmb",
        model="Qwen3.8-27B-Q4_0",
        user_message="how does a local model feel?",
    )
    assert "Call tools immediately" not in out
    assert "file_write" not in out
    assert "No tools" in out


def test_apply_local_body_strips_tools_on_trivia():
    body = {
        "messages": [{"role": "user", "content": "1 + 1"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "file_write", "parameters": {}},
            }
        ],
        "max_tokens": 8000,
        "tool_choice": "required",
    }
    out = apply_local_body_optimize(
        body,
        provider="rmb",
        model="Qwen3.5-4B",
        base_url="http://127.0.0.1:8787/v1",
        user_message="1 + 1",
        step_index=0,
    )
    assert "tools" not in out
    assert "tool_choice" not in out
    assert int(out["max_tokens"]) <= 256
    assert (out.get("chat_template_kwargs") or {}).get("enable_thinking") is True


def test_apply_local_body_thinking_off_when_owner_sets_it(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.local_agent_optimize.local_thinking_enabled",
        lambda: False,
    )
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 800,
    }
    out = apply_local_body_optimize(
        body,
        provider="rmb",
        model="Qwen3.5-9B",
        base_url="http://127.0.0.1:8787/v1",
        user_message="hi",
        step_index=0,
    )
    assert (out.get("chat_template_kwargs") or {}).get("enable_thinking") is False
    assert out.get("reasoning_budget") == 0


def test_looks_like_tutorial_monologue_from_export():
    """Regression: local Qwen create-app session dumped RPB essays, zero tools."""

    essay = """
Ahmi, to create a standalone PDF viewer and editor named RemedyPDF, we need to follow these steps:

1. **RESEARCH**: Gather information on available libraries.
2. **PLAN**: Create a plan with a short checklist.
3. **BUILD**: Implement the project and verify the build.

### 1. RESEARCH
#### Libraries and Frameworks
For PDF viewing use PyPDF2 and reportlab.

### 2. PLAN
**Short Checklist**:
- Install necessary libraries.
- Create a basic GUI using tkinter.

### 3. BUILD
#### Step 1: Install Necessary Libraries
```bash
pip install PyPDF2 reportlab tkinter
```

#### Step 2: Create a Basic GUI
```python
import tkinter as tk
class RemedyPDF:
    pass
```

### Summary
We have created a basic standalone PDF viewer. The build is verified by running the script.
"""
    assert looks_like_tutorial_monologue(essay)
    assert not looks_like_tutorial_monologue("Created `app.py` and ran py_compile — OK.")
    assert not looks_like_tutorial_monologue("hi")


def test_is_local_binding_rmb():
    assert is_local_binding("rmb", "qwen", "http://127.0.0.1:8787/v1")
    assert not is_local_binding("openai", "gpt-4o", "https://api.openai.com/v1")


def test_extract_create_path_allows_spaces():
    from remedy.core.local_agent_optimize import extract_create_path

    p = extract_create_path(
        "Create calculator at C:/Users/Administrator/Documents/Remedy Projects/New Project/calculator.py"
    )
    assert p is not None
    assert "Remedy Projects" in p
    assert p.endswith("calculator.py")


@pytest.mark.asyncio
async def test_bootstrap_writes_runnable_calculator(tmp_path):
    from pathlib import Path

    from remedy.core.llm_binding import LlmBinding, set_llm_binding
    from remedy.core.local_agent_optimize import maybe_bootstrap_local_create

    set_llm_binding(
        LlmBinding(
            provider="rmb",
            model="Qwen2.5-Coder-7B",
            base_url="http://127.0.0.1:8787/v1",
            api_key="rmb",
        )
    )
    out = tmp_path / "calculator.py"
    msg = f"Create calculator at {out.as_posix()} with add/sub/mul/div and main print add(2,3)"

    class R:
        def effective_project_path(self):
            return str(tmp_path)

        def resolve_tool_path(self, path, for_write=False):
            return Path(path)

    res = await maybe_bootstrap_local_create(R(), msg)
    assert res and "Created" in res
    assert out.is_file()
    import subprocess

    p = subprocess.run(["python", str(out)], capture_output=True, text=True, timeout=10)
    assert p.returncode == 0
    assert p.stdout.strip() == "5"


@pytest.mark.asyncio
async def test_bootstrap_skips_in_plan_mode(tmp_path):
    from pathlib import Path

    from remedy.core.local_agent_optimize import maybe_bootstrap_local_create
    from remedy.core.turn_context import begin_turn, end_turn

    out = tmp_path / "calculator.py"
    msg = f"Create calculator at {out.as_posix()} with add/sub/mul/div"

    class R:
        _plan_mode = True

        def effective_project_path(self):
            return str(tmp_path)

        def resolve_tool_path(self, path, for_write=False):
            return Path(path)

    toks = begin_turn("plan-boot", project_raw=str(tmp_path), active_path=".", plan_mode=True)
    try:
        res = await maybe_bootstrap_local_create(R(), msg)
    finally:
        end_turn("plan-boot", *toks)
    assert res is None
    assert not out.exists()

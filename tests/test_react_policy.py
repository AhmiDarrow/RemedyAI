"""Unit tests for ReAct policy helpers (tool gating, pseudo-tools, fingerprints)."""

from __future__ import annotations

import json

from remedy.core.react_policy import (
    _DEFAULT_SYSTEM_PROMPT,
    RECOVERY_NUDGE,
    SPEED_BATCH_NUDGE,
    batch_has_tool_errors,
    is_serial_explore_batch,
    looks_like_pseudo_tools,
    message_wants_tools,
    parse_pseudo_tool_calls,
    recovered_tool_call_is_complete,
    recovery_nudge_message,
    speed_batch_nudge_message,
    tool_call_fingerprint,
    tool_content_is_error,
)


def test_serial_explore_batch_detection() -> None:
    one_read = [{"function": {"name": "file_read", "arguments": "{}"}}]
    assert is_serial_explore_batch(one_read) is True
    two_reads = [
        {"function": {"name": "file_read", "arguments": "{}"}},
        {"function": {"name": "file_read", "arguments": "{}"}},
    ]
    assert is_serial_explore_batch(two_reads) is False
    assert is_serial_explore_batch([{"function": {"name": "file_edit"}}]) is False
    assert is_serial_explore_batch([]) is False
    assert "batch" in SPEED_BATCH_NUDGE.lower() or "tool_calls" in SPEED_BATCH_NUDGE
    msg = speed_batch_nudge_message()
    assert msg["role"] == "user"
    assert "Speed" in msg["content"] or "speed" in msg["content"].lower()


def test_message_wants_tools_chat_vs_code() -> None:
    assert message_wants_tools("hello!") is False
    assert message_wants_tools("what skills do you have?") is False
    assert message_wants_tools("list the files in src/") is True
    assert message_wants_tools("please review the codebase architecture") is True
    # Frozenset early-out greets / acks still False
    for g in ("hi", "thanks", "ok", "great"):
        assert message_wants_tools(g) is False, g
    # Action kicks still True (must not be swallowed by short-set)
    assert message_wants_tools("proceed") is True
    assert message_wants_tools("continue") is True
    assert message_wants_tools("sounds good") is True


def test_message_wants_tools_action_kicks() -> None:
    """Session stuck log: short proceed/continue must enable tools (not force_answer)."""
    for msg in (
        "proceed",
        "proceed with all fixes",
        "continue",
        "go ahead",
        "do it",
        "keep going",
        "you may proceed out of plan mode",
        "it doesn't look like you are doing anything",
        "switch to build",
        "leave plan mode",
        # 2026-07-25 assets session: affirm + progress pings must keep tools on
        "go with your suggestions",
        "progress?",
        "eta",
        "I need an eta",
        "do that",
        "sounds good",
        "troubleshoot why",
        "you don't seem to be able to complete this task",
        "process the assets",
        # 2026-07-28 computer-use: browse kicks must enable tools
        "goto gmail",
        "bring up google",
        "go to youtube",
        "open gmail",
        "gta 5 wiki show me it",
    ):
        assert message_wants_tools(msg) is True, msg
    # Still skip pure chit-chat
    assert message_wants_tools("hi") is False
    assert message_wants_tools("thanks") is False
    assert message_wants_tools("ok") is False


def test_history_suggests_open_work_keeps_agency() -> None:
    """Short follow-ups with no keywords still enable tools after prior tool use."""
    from remedy.core.react_policy import history_suggests_open_work, looks_like_false_progress
    from remedy.core.react_stream import should_enable_tools

    history = [
        {"role": "user", "content": "fix the logos"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "remedy_icon.png"},
    ]
    assert history_suggests_open_work(history) is True
    tools = [
        {
            "type": "function",
            "function": {"name": "list_dir", "parameters": {"type": "object"}},
        }
    ]
    # Even a bare affirmation that might miss regex still keeps tools via history.
    assert (
        should_enable_tools(
            "please",
            tools,
            has_attachments=False,
            history=history,
        )
        is True
    )
    # Soft affirm mid-task must keep agency (session bug 2026-07-28).
    assert should_enable_tools("ok", tools, has_attachments=False, history=history) is True
    assert should_enable_tools("okay", tools, has_attachments=False, history=history) is True
    assert should_enable_tools("sure", tools, has_attachments=False, history=history) is True
    # Pure social still tool-free even with open history.
    assert should_enable_tools("thanks", tools, has_attachments=False, history=history) is False
    assert should_enable_tools("hi", tools, has_attachments=False, history=history) is False
    # Without open history, soft affirm stays chat-only.
    assert should_enable_tools("ok", tools, has_attachments=False, history=None) is False
    assert looks_like_false_progress("Processing both logos now.") is True
    assert looks_like_false_progress(
        "Checking ComfyUI status and hardware so we can finish video-gen setup."
    ) is True
    assert looks_like_false_progress("Here is the final result.") is False
    assert message_wants_tools("sorry pick up where you left off") is True
    # 2026-07-28: narrated Gmail open without tool_calls
    assert looks_like_false_progress(
        "I'll try opening **Gmail** in the Browser rail now."
    ) is True
    assert looks_like_false_progress(
        "I'll navigate to https://www.google.com in the Browser rail."
    ) is True
    assert looks_like_false_progress(
        "Let me bring up Google there now."
    ) is True


def test_pseudo_tool_parse_and_log(caplog) -> None:
    text = 'file_read("README.md") && list_dir("src")'
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "file_read"
    args0 = json.loads(calls[0]["function"]["arguments"])
    assert args0["path"] == "README.md"
    assert calls[1]["function"]["name"] == "list_dir"


def test_dsml_comfyui_status_recovery() -> None:
    """DeepSeek-style DSML dump for comfyui status must recover, not show as chat."""
    text = (
        "｜DSML｜tool_calls> <invoke name=\"comfyui\"> "
        '<parameter name="action" string="true">status</parameter> '
        "</invoke> </tool_calls>"
    )
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert calls
    assert calls[0]["function"]["name"] == "comfyui"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args.get("action") == "status"
    from remedy.core.react_policy import strip_tool_markup

    assert "tool_calls" not in strip_tool_markup(text).lower()
    assert "invoke" not in strip_tool_markup(text).lower()


def test_dsml_bash_curl_rewrites_to_comfyui() -> None:
    """Chat log failure: model dumped DSML bash_exec+curl as visible text."""
    text = (
        'tool_calls invoke name="bash_exec" '
        'invoke_parameter name="code">curl -s -o /dev/null -w "%{http_code}" '
        "http://127.0.0.1:8188/</invoke_parameter>"
    )
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert calls, "expected DSML recovery"
    assert calls[0]["function"]["name"] == "comfyui"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args.get("action") == "status"


def test_truncated_dsml_bash_not_recovered() -> None:
    """DeepSeek mid-stream cut-off must not invent empty bash_exec tools."""
    text = (
        "Ahmi, I need to dig into why this specific call is failing...\n\n"
        "<｜DSML｜tool_calls>\n"
        '<｜DSML｜invoke name="bash_'
    )
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert calls == [], "truncated DSML must not yield executable tool calls"
    incomplete = {
        "function": {"name": "bash_exec", "arguments": json.dumps({"command": ""})},
    }
    assert recovered_tool_call_is_complete(incomplete) is False
    complete = {
        "function": {
            "name": "bash_exec",
            "arguments": json.dumps({"command": "echo hi"}),
        },
    }
    assert recovered_tool_call_is_complete(complete) is True


def test_dsml_list_dir_comfy_hunt_collapses_to_locate() -> None:
    """Model spam: list_dir + bash where/dir looking for ComfyUI on disk."""
    text = (
        "Let me look for the ComfyUI installation on this machine.\n"
        '<tool_calls> <invoke name="list_dir"> '
        '<parameter name="relative_path" string="false">'
        r"C:\Users\Administrator\ComfyUI</parameter> </invoke> "
        '<invoke name="list_dir"> <parameter name="relative_path" string="false">'
        "C:</parameter> </invoke> "
        '<invoke name="bash_exec"> <parameter name="command" string="true">'
        r"where comfyui 2>nul || dir /s /b C:\ComfyUI* 2>nul"
        "</parameter> </invoke> </tool_calls>"
    )
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "comfyui"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args.get("action") == "locate"


def test_message_wants_comfyui() -> None:
    assert message_wants_tools("use local comfyui to generate an image") is True
    assert message_wants_tools("generate an image for me") is True


def test_tool_call_fingerprint_stable() -> None:
    a = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    b = {
        "function": {
            "name": "file_read",
            "arguments": {"path": "a.py"},
        }
    }
    assert tool_call_fingerprint(a) == tool_call_fingerprint(b)


def test_system_prompt_has_recovery_contract() -> None:
    assert "Recovery" in _DEFAULT_SYSTEM_PROMPT
    assert "list_dir" in _DEFAULT_SYSTEM_PROMPT
    assert "Suggestion" in _DEFAULT_SYSTEM_PROMPT


def test_tool_content_is_error_variants() -> None:
    assert tool_content_is_error(
        "Error [NOT_FOUND:file_read]: file not found: missing.py\nSuggestion: list_dir"
    )
    assert tool_content_is_error("Blocked by security policy: rm -rf")
    assert tool_content_is_error('{"ok": false, "error": "nope", "code": "X"}')
    assert tool_content_is_error("exit_code=1\ncwd=/tmp\nstderr:\nbad")
    assert not tool_content_is_error("exit_code=0\ncwd=/tmp\nok")
    assert not tool_content_is_error("file contents here")
    assert not tool_content_is_error("")
    assert not tool_content_is_error(None)


def test_batch_has_tool_errors_and_nudge() -> None:
    ok = {"role": "tool", "tool_call_id": "1", "content": "hello world"}
    bad = {
        "role": "tool",
        "tool_call_id": "2",
        "content": "Error [NOT_FOUND:file_read]: missing",
    }
    assert batch_has_tool_errors([ok]) is False
    assert batch_has_tool_errors([ok, bad]) is True
    nudge = recovery_nudge_message()
    assert nudge["role"] == "user"
    assert nudge["content"] == RECOVERY_NUDGE
    assert "Recover" in RECOVERY_NUDGE

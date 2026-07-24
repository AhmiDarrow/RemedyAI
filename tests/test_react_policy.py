"""Unit tests for ReAct policy helpers (tool gating, pseudo-tools, fingerprints)."""

from __future__ import annotations

import json

from remedy.core.react_policy import (
    RECOVERY_NUDGE,
    _DEFAULT_SYSTEM_PROMPT,
    batch_has_tool_errors,
    looks_like_pseudo_tools,
    message_wants_tools,
    parse_pseudo_tool_calls,
    recovery_nudge_message,
    tool_call_fingerprint,
    tool_content_is_error,
)


def test_message_wants_tools_chat_vs_code() -> None:
    assert message_wants_tools("hello!") is False
    assert message_wants_tools("what skills do you have?") is False
    assert message_wants_tools("list the files in src/") is True
    assert message_wants_tools("please review the codebase architecture") is True


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

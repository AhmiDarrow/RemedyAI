"""Special-token tool markup must be caught, not shown to the user.

``looks_like_pseudo_tools`` recognised ``<tool_call>`` but not the
special-token spellings ``<|tool_call>`` / ``<|tool_call|>`` — the ``|`` broke
the pattern. A model trained on that convention therefore had its raw markup
surfaced as the answer. Seen live from a local fine-tune on a turn with no
tools armed:

    <|tool_call>call:Bash{command:<|"|>powershell -Command "Get-ChildItem ...

Prose that merely mentions tools, or uses a shell pipe, must still pass
through untouched.
"""

from __future__ import annotations

import pytest

from remedy.core.react_policy import looks_like_pseudo_tools

LEAKED = [
    '<|tool_call>call:Bash{command:<|"|>powershell -Command "ls"}',
    '<|tool_call|>{"name":"file_write","arguments":{}}',
    '<tool_call>{"name":"x"}</tool_call>',
    "<|function_calls|>",
    "<| tool_call |>",
]

CLEAN = [
    "READY",
    "The file has been written successfully.",
    "Use the | pipe operator in bash, e.g. ls | grep x",
    "I called the tool and it worked.",
    "",
]


@pytest.mark.parametrize("text", LEAKED)
def test_leaked_markup_is_detected(text: str) -> None:
    assert looks_like_pseudo_tools(text)


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert not looks_like_pseudo_tools(text)


TRUNCATED_FENCE = [
    '```json\n{\n "',            # observed live: a 12-char final answer
    '```json\n{\n  "name"',
    '```json\n{',
    "```json",
    '```\n{"name": "x"',
]

INTACT_PROSE = [
    "Here is some ```json in prose",
    "```python\nprint(1)\n```",
    '```json\n{"port": 8123}\n```\nThe port is 8123.',
    "I wrote the file and ran it; output was 34.",
]


@pytest.mark.parametrize("text", TRUNCATED_FENCE)
def test_dangling_json_fence_is_truncated_markup(text: str) -> None:
    """A model that began a JSON tool call and stopped must not 'answer' with it.

    Observed live from a local model on a no-tools turn: the entire reply was
    12 characters, '```json\n{\n "', surfaced to the user as the answer.
    """
    assert looks_like_pseudo_tools(text)


@pytest.mark.parametrize("text", INTACT_PROSE)
def test_closed_fences_and_prose_are_left_alone(text: str) -> None:
    """A closed code block, or prose mentioning ```json, is a real answer."""
    assert not looks_like_pseudo_tools(text)


FENCED_CALL = '```json\n{\n "name": "file_read",\n "arguments": {"path": "x.py"}\n}\n```'


def test_fenced_tool_call_is_stripped_from_an_answer() -> None:
    """Detection flagged these, but nothing removed them.

    A model told "do not use any tools" replied with a complete fenced JSON
    call and it was shown to the owner verbatim as the answer.
    """
    from remedy.core.react_policy import strip_tool_markup

    assert strip_tool_markup(FENCED_CALL).strip() == ""


def test_prose_around_a_fenced_call_survives() -> None:
    from remedy.core.react_policy import strip_tool_markup

    out = strip_tool_markup('Sure.\n```json\n{"name":"file_read","arguments":{}}\n```')
    assert out.strip() == "Sure."


def test_ordinary_code_block_is_not_a_tool_call() -> None:
    from remedy.core.react_policy import strip_tool_markup

    src = "```python\nprint(1)\n```"
    assert strip_tool_markup(src).strip() == src


@pytest.mark.parametrize(
    "text",
    [FENCED_CALL, '{"name":"file_read","arguments":{"path":"a"}}'],
)
def test_pure_tool_call_blob_detected(text: str) -> None:
    from remedy.core.react_policy import is_pure_tool_call_blob

    assert is_pure_tool_call_blob(text)


@pytest.mark.parametrize(
    "text",
    ["READY", "The port is 8123.", "```python\nprint(1)\n```", ""],
)
def test_real_answers_are_not_blobs(text: str) -> None:
    from remedy.core.react_policy import is_pure_tool_call_blob

    assert not is_pure_tool_call_blob(text)

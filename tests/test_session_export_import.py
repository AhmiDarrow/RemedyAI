"""Exporting a session to a file and reading it back.

This is how a conversation leaves Remedy — to keep, to move to another machine,
to hand to someone else. Two things carry the weight.

The round trip has to hold: whatever the formatter writes, the parser must read
back as the same conversation. They are 200 lines apart in the same file and
nothing else checks they still agree.

And an export is a file that leaves the machine. A bearer token or an API key
that was pasted into a chat must not travel with it, so the redaction is not a
nicety here — it is the difference between a shareable transcript and a leak.
"""

from __future__ import annotations

import pytest

from remedy.memory.session_io import (
    ParsedMessage,
    format_session_markdown,
    format_session_txt,
    parse_session_text,
    safe_filename_stem,
)


def msg(role, content, **kw):
    return {"role": role, "content": content, **kw}


CHAT = [
    msg("user", "what is on this week?"),
    msg("assistant", "The dentist on Thursday, and the bins on Tuesday."),
    msg("user", "move the dentist"),
]


def export(messages=None, **kw):
    return format_session_txt(
        title=kw.pop("title", "This week"),
        session_id=kw.pop("session_id", "sess-1"),
        messages=CHAT if messages is None else messages,
        **kw,
    )


# --- the round trip ----------------------------------------------------------


def test_a_conversation_survives_export_and_import():
    parsed = parse_session_text(export())
    assert [(m.role, m.content) for m in parsed.messages] == [
        ("user", "what is on this week?"),
        ("assistant", "The dentist on Thursday, and the bins on Tuesday."),
        ("user", "move the dentist"),
    ]


def test_the_title_survives():
    assert parse_session_text(export(title="Planning the week")).title == "Planning the week"


def test_the_source_session_id_survives():
    """So an import can say which conversation it came from."""
    assert parse_session_text(export(session_id="sess-42")).source_session_id == "sess-42"


def test_the_model_and_agent_survive():
    parsed = parse_session_text(export(model="claude-opus-5", agent="Remedy"))
    assert parsed.model == "claude-opus-5"
    assert parsed.agent == "Remedy"


def test_per_message_metadata_survives():
    text = export([msg("assistant", "hello", model="gpt-5", agent="Remedy")])
    m = parse_session_text(text).messages[0]
    assert m.model == "gpt-5"
    assert m.agent == "Remedy"


def test_a_multi_line_message_keeps_its_lines():
    body = "first line\nsecond line\n\nfourth after a gap"
    assert parse_session_text(export([msg("user", body)])).messages[0].content == body


def test_a_message_containing_a_role_marker_does_not_split_the_conversation():
    """Someone pasting the export format back into a chat must not corrupt it."""
    parsed = parse_session_text(export([msg("user", "I typed ===== USER ===== once")]))
    assert len(parsed.messages) >= 1


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_every_role_round_trips(role):
    assert parse_session_text(export([msg(role, "body")])).messages[0].role == role


def test_a_message_object_works_as_well_as_a_dict():
    """Exports are called with both ORM rows and plain dicts."""

    class Row:
        role = "user"
        content = "from an object"
        model = None
        agent = None
        created_at = None

    assert "from an object" in export([Row()])


def test_a_non_string_body_does_not_break_the_export():
    assert "12345" in export([msg("user", 12345)])


# --- redaction: the export leaves the machine --------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH",
        "Bearer abc123def456ghi789",
        "api_key=super-secret-value-here",
    ],
)
def test_a_credential_pasted_into_a_chat_does_not_travel_with_the_export(secret):
    text = export([msg("user", f"use this: {secret}")])
    assert secret not in text


def test_redaction_still_happens_if_the_redactor_is_unavailable(monkeypatch):
    """Fail closed: no redactor is not permission to write the key out."""
    import remedy.core.metabolism.redact as R

    def boom(_t):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(R, "redact_text", boom)
    text = export([msg("user", "key sk-ant-api03-AAAABBBBCCCCDDDDEEEE")])
    assert "sk-ant-api03-AAAABBBBCCCCDDDDEEEE" not in text


# --- size: an export must not freeze the UI ----------------------------------


def test_a_tool_dump_is_cut_down_to_a_stub():
    """Multi-megabyte tool output is the usual reason an export hangs."""
    text = export([msg("tool", "x" * 50_000)])
    assert "tool output truncated" in text
    assert len(text) < 10_000


def test_a_tool_dump_is_flattened_to_one_line():
    text = export([msg("tool", "line one\nline two\nline three")])
    assert "line one line two line three" in text


def test_an_enormous_message_is_truncated_and_says_so():
    text = export([msg("assistant", "y" * 100_000)])
    assert "export truncated" in text


def test_an_inline_base64_image_is_replaced_by_a_stub():
    body = "here it is ![shot](data:image/png;base64," + "A" * 400 + ")"
    text = export([msg("assistant", body)])
    assert "A" * 400 not in text
    assert "image omitted" in text


def test_a_bare_base64_data_uri_is_replaced_too():
    body = "raw: data:image/png;base64," + "B" * 400
    text = export([msg("assistant", body)])
    assert "B" * 400 not in text


def test_a_very_long_conversation_exports_the_recent_end_and_says_so():
    text = export([msg("user", f"message {i}") for i in range(2500)])
    assert "older omitted for size" in text
    assert "message 2499" in text
    assert "message 0\n" not in text


def test_a_normal_message_is_not_touched():
    body = "just an ordinary reply about the dentist"
    assert body in export([msg("assistant", body)])


# --- importing other shapes --------------------------------------------------


def test_a_legacy_markdown_export_still_imports():
    """Files exported by older builds have to keep working."""
    parsed = parse_session_text(
        "# Old Session\n\n**User**\n\nhello there\n\n**Assistant**\n\nhello back\n"
    )
    assert [m.role for m in parsed.messages] == ["user", "assistant"]
    assert parsed.messages[0].content == "hello there"


def test_a_plain_text_file_becomes_one_message():
    """Dropping any old notes file in should start a conversation about it."""
    parsed = parse_session_text("# My Notes\n\nbuy milk\ncall the dentist\n")
    assert parsed.title == "My Notes"
    assert len(parsed.messages) == 1
    assert "buy milk" in parsed.messages[0].content


def test_a_plain_file_with_no_heading_still_imports():
    parsed = parse_session_text("just some thoughts\nand more of them\n")
    assert parsed.messages[0].content.startswith("just some thoughts")


def test_windows_line_endings_are_handled():
    parsed = parse_session_text("# Notes\r\n\r\nbody here\r\n")
    assert "body here" in parsed.messages[0].content


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n"])
def test_an_empty_file_is_refused_rather_than_imported_as_nothing(text):
    with pytest.raises(ValueError, match="Empty"):
        parse_session_text(text)


def test_a_heading_with_no_body_is_refused():
    """An import that silently produces an empty session is worse than an error."""
    with pytest.raises(ValueError, match="No message content"):
        parse_session_text("# Just A Title\n\n")


# --- the markdown export -----------------------------------------------------


def test_the_markdown_export_names_the_session():
    out = format_session_markdown(title="This week", session_id="s1", messages=CHAT)
    assert out.startswith("# This week")


def test_the_markdown_export_carries_every_message():
    out = format_session_markdown(title="t", session_id="s1", messages=CHAT)
    for m in CHAT:
        assert m["content"] in out


def test_a_markdown_export_reimports():
    out = format_session_markdown(title="This week", session_id="s1", messages=CHAT)
    assert len(parse_session_text(out).messages) == 3


# --- filenames ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Planning the week", "Planning the week"),
        ("notes.v2", "notes.v2"),
        ("a/b\\c:d", "a_b_c_d"),
        ("what?! *really*", "what__ _really_"),  # spaces are legal in filenames
        ("", "Session"),
        ("   ", "Session"),
    ],
)
def test_a_title_becomes_a_filename_a_filesystem_will_accept(title, expected):
    assert safe_filename_stem(title) == expected


def test_a_long_title_is_bounded():
    assert len(safe_filename_stem("x" * 500)) <= 60


def test_the_bound_is_adjustable():
    assert len(safe_filename_stem("x" * 500, max_len=10)) <= 10


def test_a_parsed_message_defaults_are_sane():
    m = ParsedMessage(role="user", content="hi")
    assert m.model is None and m.agent is None and m.created_at is None

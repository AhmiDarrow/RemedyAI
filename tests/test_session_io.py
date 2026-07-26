"""Session export/import text formatting."""

from __future__ import annotations

from remedy.memory.session_io import _export_content, format_session_txt


def test_export_strips_huge_data_uris_and_caps() -> None:
    blob = "intro\n" + "![x](data:image/png;base64," + ("A" * 800) + ")\n" + ("z" * 200_000)
    out = _export_content(blob)
    assert "omitted" in out.lower() or "truncated" in out.lower()
    assert "data:image" not in out or len(out) < 150_000
    assert len(out) < 150_000


def test_format_session_txt_basic() -> None:
    body = format_session_txt(
        title="T",
        session_id="sid-1",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        model="grok-4.5",
    )
    assert "===== USER =====" in body
    assert "===== ASSISTANT =====" in body
    assert "hi" in body
    assert "hello" in body

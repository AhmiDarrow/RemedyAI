"""Session export/import text formatting."""

from __future__ import annotations

from remedy.memory.session_io import _export_content, format_session_txt


def test_export_strips_huge_data_uris_and_caps() -> None:
    blob = "intro\n" + "![x](data:image/png;base64," + ("A" * 800) + ")\n" + ("z" * 200_000)
    out = _export_content(blob)
    assert "omitted" in out.lower() or "truncated" in out.lower()
    assert "data:image" not in out or len(out) < 60_000
    assert len(out) < 60_000


def test_export_tool_role_is_aggressively_capped() -> None:
    dump = "ok " + ("x" * 50_000)
    out = _export_content(dump, role="tool")
    assert len(out) < 3_000
    assert "truncated" in out.lower()


def test_export_redacts_secret_shaped_content() -> None:
    """Portable exports must not ship API keys / bearer tokens verbatim."""
    secret = "config api_key=sk-abcdefghijklmnopqrstuvwxyz012345 and Bearer ya29.secretvalueHERE"
    out = _export_content(secret, role="user")
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "ya29.secretvalueHERE" not in out
    assert "[redacted]" in out

    body = format_session_txt(
        title="Secrets",
        session_id="sid-sec",
        messages=[
            {"role": "user", "content": "my key is sk-abcdefghijklmnopqrstuvwxyz999"},
            {
                "role": "tool",
                "content": "Authorization: Bearer sk-proj-ABCDEFGHIJKLMNOPQRSTUV",
            },
        ],
    )
    assert "sk-abcdefghijklmnopqrstuvwxyz999" not in body
    assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUV" not in body
    assert "Bearer sk-proj" not in body


def test_export_redacts_pem_connection_and_provider_shapes() -> None:
    """Regression: PEM, DB URLs, Slack/GH tokens must not leave via export."""
    from remedy.memory.session_io import format_session_markdown

    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fakekeymaterial\n"
        "-----END PRIVATE KEY-----"
    )
    payloads = [
        pem,
        "postgres://user:SuperSecretPass@db.internal:5432/app",
        "mongodb+srv://admin:hunter2@cluster0.example.net/db",
        "xoxb-1234567890-abcdefghij",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AIzaSyA-fakeGoogleApiKey0123456789abcd",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart",
    ]
    for raw in payloads:
        out = _export_content(raw, role="assistant")
        # Core secret material must not survive
        assert "SuperSecretPass" not in out
        assert "hunter2" not in out
        assert "xoxb-1234567890" not in out
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in out
        assert "AIzaSyA-fakeGoogleApiKey0123456789abcd" not in out
        assert "BEGIN PRIVATE KEY" not in out or "[redacted]" in out
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out or "[redacted]" in out

    md = format_session_markdown(
        title="leak",
        session_id="sid-md",
        messages=[{"role": "user", "content": payloads[1]}],
    )
    assert "SuperSecretPass" not in md
    assert "[redacted]" in md


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

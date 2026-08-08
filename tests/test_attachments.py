"""Session attachment helpers and multimodal message building."""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces.attachments import (
    build_attachment_prompt_block,
    chat_media_display_path,
    build_multimodal_user_content,
    filter_jailed_attachments,
    inject_text_file_snippets,
    is_image,
    is_path_under_attachments,
    is_probably_text,
    markdown_image_embed,
    sanitize_filename,
    save_upload,
)


def test_sanitize_filename():
    assert ".." not in sanitize_filename("../../etc/passwd")
    assert sanitize_filename("ok file.py") == "ok file.py"


def test_chat_media_display_path_prefers_attachments_relative(tmp_path: Path):
    home = tmp_path / "home"
    abs_path = home / "attachments" / "sess-a" / "shot.png"
    abs_path.parent.mkdir(parents=True)
    abs_path.write_bytes(b"x")
    rel = chat_media_display_path(abs_path, home_dir=home)
    assert rel == "attachments/sess-a/shot.png"
    # Outside home → absolute posix-ish path
    other = tmp_path / "elsewhere" / "x.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"y")
    out = chat_media_display_path(other, home_dir=home)
    assert "elsewhere" in out.replace("\\", "/")


def test_save_upload_and_text_inject(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    data = b"print('hello')\n"
    meta = save_upload(
        session_id="sess1",
        filename="hello.py",
        data=data,
        content_type="text/x-python",
        home_dir=home,
    )
    assert meta["is_text"] is True
    assert Path(meta["path"]).is_file()
    assert Path(meta["path"]).read_bytes() == data

    content = build_multimodal_user_content(
        "look at this", [meta], home_dir=home, session_id="sess1"
    )
    assert isinstance(content, str)
    assert "hello.py" in content
    assert "print('hello')" in content


def test_image_multimodal_parts(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    # Minimal 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="sess2",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    assert meta["is_image"] is True
    content = build_multimodal_user_content(
        "what is this?", [meta], home_dir=home, session_id="sess2"
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content)


def test_mime_helpers():
    assert is_probably_text("text/plain", "a.txt")
    assert is_probably_text("application/octet-stream", "main.py")
    assert is_image("image/png")


def test_markdown_image_embed_spaces_and_slashes():
    assert markdown_image_embed("a.png", r"C:\tmp\a.png") == "![a.png](C:/tmp/a.png)"
    emb = markdown_image_embed("shot.png", r"C:\Users\Me\shot 1.png")
    assert emb.startswith("![shot.png](<")
    assert "shot 1.png" in emb
    assert emb.endswith(">)")


def test_attachment_block_embeds_images_for_chat_display(tmp_path: Path):
    """Images must appear as markdown so the UI can render them (any model)."""
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="sess-img",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    block = build_attachment_prompt_block([meta], home_dir=home)
    assert "![" in block
    assert "dot.png" in block
    # Prefer stable attachments/… relative src for desktop /api/media
    assert "attachments/" in block.replace("\\", "/")
    assert "dot.png" in block
    # Absolute path still listed for tools/file_read
    assert meta["path"].replace("\\", "/") in block.replace("\\", "/")


def test_save_upload_keeps_original_name_on_reupload(tmp_path: Path):
    """Re-drop of the same filename must not become notes_1.txt / notes_3.txt."""
    home = tmp_path / "home"
    home.mkdir()
    m1 = save_upload(
        session_id="sess-x",
        filename="notes.txt",
        data=b"first",
        content_type="text/plain",
        home_dir=home,
    )
    m2 = save_upload(
        session_id="sess-x",
        filename="notes.txt",
        data=b"second",
        content_type="text/plain",
        home_dir=home,
    )
    assert m1["name"] == "notes.txt"
    assert m2["name"] == "notes.txt"
    assert m1["path"] == m2["path"]
    assert Path(m2["path"]).read_bytes() == b"second"
    assert "_3" not in m2["name"]
    assert "_1" not in m2["name"]


def test_attachment_path_jail_blocks_forged_secret_path(tmp_path: Path):
    """Client-forged AttachmentRef paths outside attachments tree must not inject."""
    home = tmp_path / "home"
    home.mkdir()
    secret = tmp_path / "auth" / "provider_keys.json"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"api_key":"sk-leaked-secret-value-12345"}', encoding="utf-8")

    # Legitimate attachment
    legit = save_upload(
        session_id="sess-jail",
        filename="ok.txt",
        data=b"hello attach",
        content_type="text/plain",
        home_dir=home,
    )
    assert is_path_under_attachments(legit["path"], home_dir=home, session_id="sess-jail")
    assert not is_path_under_attachments(secret, home_dir=home, session_id="sess-jail")

    forged = {
        "name": "provider_keys.json",
        "path": str(secret),
        "mime": "application/json",
        "is_text": True,
        "is_image": False,
        "size": secret.stat().st_size,
    }
    filtered = filter_jailed_attachments(
        [legit, forged], home_dir=home, session_id="sess-jail"
    )
    assert len(filtered) == 1
    assert filtered[0]["name"] == "ok.txt"

    snippets = inject_text_file_snippets(
        [legit, forged], home_dir=home, session_id="sess-jail"
    )
    assert "hello attach" in snippets
    assert "sk-leaked" not in snippets
    assert "provider_keys" not in snippets

    content = build_multimodal_user_content(
        "see files",
        [legit, forged],
        home_dir=home,
        session_id="sess-jail",
    )
    blob = content if isinstance(content, str) else str(content)
    assert "sk-leaked" not in blob


def test_svg_not_sent_as_vision_payload(tmp_path: Path):
    """SVG (scriptable) must not become image_url multimodal parts."""
    home = tmp_path / "home"
    home.mkdir()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    meta = save_upload(
        session_id="sess-svg",
        filename="x.svg",
        data=svg,
        content_type="image/svg+xml",
        home_dir=home,
    )
    # Marked image by mime prefix — still refused for vision bytes
    meta["is_image"] = True
    content = build_multimodal_user_content(
        "what is this?",
        [meta],
        home_dir=home,
        session_id="sess-svg",
    )
    # String only (no image_url parts) or list without image_url
    if isinstance(content, list):
        assert not any(p.get("type") == "image_url" for p in content)
    else:
        assert isinstance(content, str)


def test_attachment_jail_is_session_scoped(tmp_path: Path):
    """With session_id set, sibling session attachments must not inject."""
    home = tmp_path / "home"
    home.mkdir()
    a = save_upload(
        session_id="sess-A",
        filename="a-secret.txt",
        data=b"session-A-only-payload",
        content_type="text/plain",
        home_dir=home,
    )
    b = save_upload(
        session_id="sess-B",
        filename="b-ok.txt",
        data=b"session-B-ok",
        content_type="text/plain",
        home_dir=home,
    )
    # Cross-session path is under attachments root but must fail session jail
    assert is_path_under_attachments(a["path"], home_dir=home, session_id="sess-A")
    assert not is_path_under_attachments(a["path"], home_dir=home, session_id="sess-B")
    assert is_path_under_attachments(b["path"], home_dir=home, session_id="sess-B")

    forged = {
        "name": "a-secret.txt",
        "path": a["path"],
        "mime": "text/plain",
        "is_text": True,
        "is_image": False,
        "size": a["size"],
    }
    filtered = filter_jailed_attachments(
        [b, forged], home_dir=home, session_id="sess-B"
    )
    assert len(filtered) == 1
    assert filtered[0]["name"] == "b-ok.txt"

    snippets = inject_text_file_snippets(
        [b, forged], home_dir=home, session_id="sess-B"
    )
    assert "session-B-ok" in snippets
    assert "session-A-only-payload" not in snippets


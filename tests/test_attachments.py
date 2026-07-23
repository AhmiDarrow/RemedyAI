"""Session attachment helpers and multimodal message building."""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces.attachments import (
    build_multimodal_user_content,
    is_image,
    is_probably_text,
    save_upload,
    sanitize_filename,
)


def test_sanitize_filename():
    assert ".." not in sanitize_filename("../../etc/passwd")
    assert sanitize_filename("ok file.py") == "ok file.py"


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

    content = build_multimodal_user_content("look at this", [meta])
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
    content = build_multimodal_user_content("what is this?", [meta])
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content)


def test_mime_helpers():
    assert is_probably_text("text/plain", "a.txt")
    assert is_probably_text("application/octet-stream", "main.py")
    assert is_image("image/png")

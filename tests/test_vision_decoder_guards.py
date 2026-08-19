"""Feeding a screenshot to the local vision model — and only the local one.

The decoder posts the image, base64-encoded, to whatever `base_url` it is
given. That makes the loopback check the load-bearing part: a poisoned or
misconfigured base would ship screenshots of the owner's desktop to a remote
host, and screenshots are the single most revealing thing Remedy holds.

Everything here is offline. No request is ever made, because every test either
stops at the guard or at a missing file.
"""

from __future__ import annotations

import base64

import pytest

from remedy.vision.decoder import _guess_mime, _image_data_url, decode_image, decode_images

PNG = bytes.fromhex("89504e470d0a1a0a") + b"fake png body"


@pytest.fixture()
def shot(tmp_path):
    p = tmp_path / "screenshot.png"
    p.write_bytes(PNG)
    return p


# --- the loopback boundary ----------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "https://evil.example/v1",
        "http://192.168.0.50:8080/v1",
        "http://10.0.0.5/v1",
        "https://api.openai.com/v1",
        "http://localhost.evil.example/v1",
    ],
)
def test_a_screenshot_is_never_posted_off_the_machine(shot, base):
    """The most revealing thing she holds; it does not leave loopback."""
    out = decode_image(shot, base_url=base)
    assert out["ok"] is False
    assert "loopback" in out["error"]


@pytest.mark.parametrize("base", ["", "   ", None])
def test_no_base_url_is_refused_rather_than_guessed(shot, base):
    out = decode_image(shot, base_url=base)
    assert out["ok"] is False
    assert "loopback" in out["error"]


def test_the_refusal_does_not_carry_the_image(shot):
    out = decode_image(shot, base_url="https://evil.example/v1")
    assert base64.standard_b64encode(PNG).decode() not in str(out)
    assert out["text"] == ""


def test_the_refusal_names_the_file_so_the_caller_can_report_it(shot):
    assert decode_image(shot, base_url="https://evil.example/v1")["path"] == str(shot)


# --- a file that cannot be sent ------------------------------------------------


def test_a_missing_file_is_reported_before_any_request(tmp_path):
    out = decode_image(tmp_path / "nope.png", base_url="http://127.0.0.1:8080/v1")
    assert out["ok"] is False
    assert "missing" in out["error"].lower()


def test_an_enormous_image_is_refused_rather_than_sent(tmp_path):
    """Base64 inflates by a third; a huge frame would blow the request out."""
    big = tmp_path / "huge.png"
    big.write_bytes(b"x" * 4000)
    out = decode_image(big, base_url="http://127.0.0.1:8080/v1", max_image_bytes=100)
    assert out["ok"] is False
    assert "too large" in out["error"].lower()


def test_a_slightly_oversized_image_is_still_attempted(tmp_path):
    """Many VLMs accept more than the nominal cap; the caller may have resized."""
    p = tmp_path / "big.png"
    p.write_bytes(b"x" * 250)
    assert _image_data_url(p, 100) is not None


# --- building the payload -------------------------------------------------------


def test_an_image_becomes_a_data_url(shot):
    url = _image_data_url(shot, 4 * 1024 * 1024)
    assert url.startswith("data:image/png;base64,")
    assert base64.standard_b64encode(PNG).decode() in url


def test_a_missing_file_has_no_data_url(tmp_path):
    assert _image_data_url(tmp_path / "nope.png", 4096) is None


def test_a_directory_is_not_an_image(tmp_path):
    assert _image_data_url(tmp_path, 4096) is None


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("shot.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("anim.gif", "image/gif"),
        ("modern.webp", "image/webp"),
    ],
)
def test_the_image_type_is_read_from_the_name(tmp_path, name, mime):
    p = tmp_path / name
    p.write_bytes(PNG)
    assert _guess_mime(p) == mime


@pytest.mark.parametrize("name", ["notes.txt", "data.bin", "noextension", "archive.zip"])
def test_something_that_is_not_an_image_falls_back_to_png(tmp_path, name):
    """A wrong-but-valid mime beats sending one the server will reject."""
    p = tmp_path / name
    p.write_bytes(PNG)
    assert _guess_mime(p) == "image/png"


# --- batches --------------------------------------------------------------------


def test_a_batch_returns_one_result_per_image(shot, tmp_path):
    second = tmp_path / "another.png"
    second.write_bytes(PNG)
    out = decode_images([shot, second], base_url="https://evil.example/v1")
    assert len(out) == 2
    assert all(r["ok"] is False for r in out)


def test_an_empty_batch_is_not_an_error():
    assert decode_images([], base_url="http://127.0.0.1:8080/v1") == []


def test_one_bad_image_does_not_lose_the_others(shot, tmp_path):
    out = decode_images(
        [tmp_path / "missing.png", shot], base_url="https://evil.example/v1"
    )
    assert len(out) == 2
    assert "missing" in out[0]["error"].lower()
    assert "loopback" in out[1]["error"]

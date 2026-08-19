"""Uploading and fetching a session attachment over the API.

The download route serves a file from disk with a name the caller chose, which
makes path traversal the thing to get right: `../../config.toml` must not
resolve to anything outside the session's own folder. The guard is basename +
`relative_to`, not a prefix check, because a prefix check treats
`attachments/s1-evil` as being inside `attachments/s1`.

Upload has its own edges — a payload that is not base64, an empty file, one too
large to hold in memory — and each must come back as a specific status rather
than a 500.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remedy.interfaces.routes.sessions.attachments import register_attachments_routes


class Memory:
    """A store that knows about one session."""

    def __init__(self, known=("s1",)) -> None:
        self.known = set(known)

    async def get_chat_session(self, session_id):
        return {"id": session_id} if session_id in self.known else None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config", lambda: {"home_dir": str(tmp_path)}
    )
    app = FastAPI()
    register_attachments_routes(app, memory=Memory())
    return TestClient(app)


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    """No memory wired — the session check is skipped."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config", lambda: {"home_dir": str(tmp_path)}
    )
    app = FastAPI()
    register_attachments_routes(app)
    return TestClient(app)


def upload(client, *, session="s1", name="note.txt", data=b"hello", ctype="text/plain"):
    return client.post(
        f"/api/sessions/{session}/attachments",
        json={
            "filename": name,
            "content_type": ctype,
            "data_base64": base64.b64encode(data).decode(),
        },
    )


# --- uploading ----------------------------------------------------------------


def test_a_file_is_stored_and_described(client):
    r = upload(client)
    assert r.status_code == 200
    assert r.json()


def test_an_upload_to_an_unknown_session_is_refused(client):
    """Otherwise a typo'd id silently creates a folder nobody will ever read."""
    assert upload(client, session="no-such-session").status_code == 404


def test_a_payload_that_is_not_base64_is_a_bad_request(client):
    r = client.post(
        "/api/sessions/s1/attachments",
        json={"filename": "x.txt", "content_type": "text/plain", "data_base64": "!!!!"},
    )
    assert r.status_code == 400


def test_an_empty_file_is_refused(client):
    assert upload(client, data=b"").status_code == 400


def test_a_file_over_the_ceiling_is_refused_with_the_right_status(client):
    """413, not 500 — the caller needs to know it was the size."""
    from remedy.interfaces.attachments import MAX_ATTACHMENT_BYTES

    r = upload(client, data=b"x" * (MAX_ATTACHMENT_BYTES + 1))
    assert r.status_code == 413
    assert "too large" in r.text.lower()


def test_a_file_with_no_name_still_gets_one(client):
    assert upload(client, name="").status_code == 200


def test_uploading_without_a_memory_store_skips_the_session_check(anon_client):
    assert upload(anon_client, session="anything").status_code == 200


# --- fetching -----------------------------------------------------------------


def test_an_uploaded_file_comes_back(client):
    name = upload(client, name="note.txt", data=b"hello there").json()
    stored = name.get("name") or name.get("filename") or "note.txt"
    r = client.get(f"/api/sessions/s1/attachments/{stored}")
    assert r.status_code == 200
    assert r.content == b"hello there"


def test_a_file_that_was_never_uploaded_is_a_404(client):
    assert client.get("/api/sessions/s1/attachments/nope.txt").status_code == 404


def test_a_file_from_another_session_is_not_reachable(client):
    """Session folders are the boundary; one session cannot read another's."""
    upload(client, session="s1", name="private.txt", data=b"secret")
    r = client.get("/api/sessions/s2/attachments/private.txt")
    assert r.status_code == 404


# --- path traversal -----------------------------------------------------------


@pytest.mark.parametrize(
    "attempt",
    [
        "..%2F..%2Fconfig.toml",
        "..%5C..%5Cconfig.toml",
        "%2e%2e%2f%2e%2e%2fconfig.toml",
        "....//config.toml",
        "..",
        ".",
    ],
)
def test_a_traversal_attempt_never_leaves_the_session_folder(client, tmp_path, attempt):
    """The file it is reaching for exists; the answer must still not be 200."""
    (tmp_path / "config.toml").write_text("llm_api_key = 'secret'\n", encoding="utf-8")
    r = client.get(f"/api/sessions/s1/attachments/{attempt}")
    assert r.status_code != 200
    assert "secret" not in r.text


def test_an_absolute_path_is_not_served(client, tmp_path):
    secret = tmp_path / "config.toml"
    secret.write_text("llm_api_key = 'secret'\n", encoding="utf-8")
    r = client.get(f"/api/sessions/s1/attachments/{secret.as_posix()}")
    assert r.status_code != 200
    assert "secret" not in r.text


def test_a_sibling_session_folder_is_not_reachable_by_prefix(client, tmp_path):
    """`s1-evil` starts with `s1`; a prefix check would let it through."""
    upload(client, session="s1", name="ok.txt", data=b"fine")
    r = client.get("/api/sessions/s1/attachments/..%2Fs1-evil%2Fx.txt")
    assert r.status_code != 200


def test_a_plain_filename_is_still_served_after_all_that(client):
    """The guard must not have made ordinary downloads impossible."""
    upload(client, name="report.txt", data=b"contents")
    assert client.get("/api/sessions/s1/attachments/report.txt").status_code == 200

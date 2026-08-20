"""The HTTP surface of the Skills Library: catalog, search, suggest, install, submit.

These routes are the only way a skill written by a stranger gets onto the
owner's machine, so the interesting properties are the refusals. A submitted
zip must not be able to write outside its extract directory, must not smuggle
`eval(` past the scanner, must not be accepted under a name that is really a
path, and must not be allowed to blow memory up by lying about its size. The
install path must turn "no such skill" into a 404 and "already installed" into
a 409 rather than a 500, because the Desktop app branches on those codes.

The read paths matter for a duller reason: every one of them resolves a home
directory and reads a signed catalog from it. Getting that home wrong means
reading — or worse, installing into — the owner's live ~/.remedy.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from remedy.interfaces.routes.skills_library import (
    _read_upload_capped,
    register_skills_library_routes,
)
from remedy.skills.library.catalog import SkillCatalogEntry, SkillsCatalog
from remedy.skills.library.install import MAX_SKILL_ZIP_BYTES
from remedy.skills.library.suggest import (
    LibraryHit,
    clear_session_suppress,
    is_suppressed,
)

# --- doubles ------------------------------------------------------------------


class Config:
    def __init__(self, home_dir: str) -> None:
        self.home_dir = home_dir


class Runtime:
    """Just enough runtime for _home() to resolve to a throwaway directory."""

    def __init__(self, home_dir: str) -> None:
        self.config = Config(home_dir)


class FakeUpload:
    """Duck-typed UploadFile: only .size and .read(n) are used."""

    def __init__(self, data: bytes, size: object = None) -> None:
        self._buf = io.BytesIO(data)
        self.size = size

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    @property
    def consumed(self) -> int:
        return self._buf.tell()


class FakeIndex:
    def __init__(self, *, source: str = "cache", size: int = 3, needs_refresh: bool = False):
        self.source = source
        self._size = size
        self.needs_refresh = needs_refresh

    def __len__(self) -> int:
        return self._size


def entry(**kw) -> SkillCatalogEntry:
    base = {
        "id": "alpha",
        "name": "alpha",
        "description": "does alpha things",
        "version": "1.0.0",
        "author": "ahmi",
        "tags": ["one"],
        "download_url": "https://github.com/AhmiDarrow/remedy-skills/releases/x.zip",
        "checksum": "sha256:" + "0" * 64,
    }
    base.update(kw)
    return SkillCatalogEntry(**base)


def catalog(*entries: SkillCatalogEntry, source: str = "cache") -> SkillsCatalog:
    cat = SkillsCatalog(skills=list(entries))
    cat.source = source
    return cat


@pytest.fixture()
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def client(home) -> TestClient:
    app = FastAPI()
    register_skills_library_routes(app, runtime=Runtime(str(home)))
    return TestClient(app)


@pytest.fixture()
def raw_client(home) -> TestClient:
    """Same app, but server exceptions come back as 500 instead of raising."""
    app = FastAPI()
    register_skills_library_routes(app, runtime=Runtime(str(home)))
    return TestClient(app, raise_server_exceptions=False)


def patch_catalog(monkeypatch, *, result=None, error: Exception | None = None) -> list[dict]:
    """Replace the lazily-imported get_skills_catalog and record its kwargs."""
    calls: list[dict] = []

    async def fake(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr("remedy.skills.library.catalog.get_skills_catalog", fake)
    return calls


# --- _read_upload_capped ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_upload_under_the_cap_is_returned_whole():
    up = FakeUpload(b"x" * 1000)
    assert await _read_upload_capped(up, max_bytes=4096) == b"x" * 1000


@pytest.mark.asyncio
async def test_an_upload_exactly_at_the_cap_is_still_accepted():
    """The check is `total > cap`, so the boundary byte must not be rejected."""
    up = FakeUpload(b"x" * 64)
    assert len(await _read_upload_capped(up, max_bytes=64)) == 64


@pytest.mark.asyncio
async def test_a_declared_size_over_the_cap_is_refused_before_anything_is_read():
    up = FakeUpload(b"x" * 10, size=MAX_SKILL_ZIP_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        await _read_upload_capped(up)
    assert exc.value.status_code == 413
    assert up.consumed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bogus", ["not-a-number", object(), [1, 2]])
async def test_an_unparseable_declared_size_is_ignored_not_fatal(bogus):
    """A hostile client can send anything as Content-Length; fall back to counting."""
    up = FakeUpload(b"x" * 10, size=bogus)
    assert await _read_upload_capped(up, max_bytes=4096) == b"x" * 10


@pytest.mark.asyncio
async def test_a_body_that_lies_about_its_size_is_still_cut_off_mid_stream():
    up = FakeUpload(b"x" * 5000, size=1)
    with pytest.raises(HTTPException) as exc:
        await _read_upload_capped(up, max_bytes=100)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_an_upload_with_no_size_attribute_at_all_is_accepted():
    class Bare:
        def __init__(self) -> None:
            self._b = io.BytesIO(b"hello")

        async def read(self, n: int = -1) -> bytes:
            return self._b.read(n)

    assert await _read_upload_capped(Bare(), max_bytes=64) == b"hello"


# --- catalog ------------------------------------------------------------------


def test_the_catalog_is_returned_as_a_plain_dict(client, monkeypatch):
    patch_catalog(monkeypatch, result=catalog(entry()))
    r = client.get("/api/skills/library/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "cache"
    assert [s["id"] for s in body["skills"]] == ["alpha"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("", False), ("?refresh=true", True), ("?refresh=false", False), ("?refresh=1", True)],
)
def test_the_refresh_flag_is_forwarded_verbatim(client, monkeypatch, query, expected):
    calls = patch_catalog(monkeypatch, result=catalog())
    assert client.get(f"/api/skills/library/catalog{query}").status_code == 200
    assert calls[0]["refresh"] is expected


def test_a_catalog_that_cannot_be_verified_is_a_502_not_a_crash(client, monkeypatch):
    """Signature failure is an upstream problem, so it must not read as a client error."""
    patch_catalog(monkeypatch, error=ValueError("Catalog signature verification failed"))
    r = client.get("/api/skills/library/catalog")
    assert r.status_code == 502
    assert "signature" in r.json()["detail"]


def test_the_catalog_is_read_from_the_runtime_home(client, monkeypatch, home):
    calls = patch_catalog(monkeypatch, result=catalog())
    client.get("/api/skills/library/catalog")
    assert calls[0]["home"] == home


def test_without_a_runtime_the_home_still_honours_remedy_home(monkeypatch, tmp_path):
    """It went straight to ~/.remedy, so a portable install read its catalog
    from — and installed skills into — the real user home."""
    calls = patch_catalog(monkeypatch, result=catalog())
    elsewhere = tmp_path / "portable-home"
    monkeypatch.setenv("REMEDY_HOME", str(elsewhere))
    app = FastAPI()
    register_skills_library_routes(app)
    TestClient(app).get("/api/skills/library/catalog")
    assert calls[0]["home"] == elsewhere.resolve()


# --- search -------------------------------------------------------------------


def test_search_reports_query_total_and_source(client, monkeypatch):
    patch_catalog(
        monkeypatch,
        result=catalog(entry(), entry(id="beta", name="beta", description="unrelated")),
    )
    body = client.get("/api/skills/library/search?q=alpha").json()
    assert set(body) == {"query", "total", "results", "source"}
    assert body["query"] == "alpha"
    assert body["total"] == 1
    assert body["source"] == "cache"
    assert body["results"][0]["name"] == "alpha"


def test_an_empty_query_returns_every_skill(client, monkeypatch):
    patch_catalog(monkeypatch, result=catalog(entry(), entry(id="beta", name="beta")))
    assert client.get("/api/skills/library/search").json()["total"] == 2


@pytest.mark.parametrize("tags", ["", "   ", ",,", " , , "])
def test_a_blank_tag_string_does_not_filter_everything_out(client, monkeypatch, tags):
    """`"".split(",")` yields `[""]`; if that reached the filter nothing would match."""
    patch_catalog(monkeypatch, result=catalog(entry()))
    assert client.get(f"/api/skills/library/search?tags={tags}").json()["total"] == 1


def test_tags_are_split_on_commas_and_stripped(client, monkeypatch):
    patch_catalog(
        monkeypatch,
        result=catalog(entry(), entry(id="beta", name="beta", tags=["two"])),
    )
    body = client.get("/api/skills/library/search?tags= two , ").json()
    assert [r["id"] for r in body["results"]] == ["beta"]


def test_the_author_filter_is_exact_not_a_substring(client, monkeypatch):
    patch_catalog(monkeypatch, result=catalog(entry(author="ahmi")))
    assert client.get("/api/skills/library/search?author=ahm").json()["total"] == 0
    assert client.get("/api/skills/library/search?author=AHMI").json()["total"] == 1


@pytest.mark.parametrize(
    ("sort_by", "first"),
    [
        ("name", "aaa"),
        ("rating", "zzz"),
        ("installs", "mmm"),
        ("nonsense", "aaa"),  # unknown keys fall back to name order, not an error
    ],
)
def test_results_are_sorted_by_the_requested_key(client, monkeypatch, sort_by, first):
    patch_catalog(
        monkeypatch,
        result=catalog(
            entry(id="1", name="zzz", rating=9.0, installs=1),
            entry(id="2", name="aaa", rating=1.0, installs=2),
            entry(id="3", name="mmm", rating=5.0, installs=99),
        ),
    )
    body = client.get(f"/api/skills/library/search?sort_by={sort_by}").json()
    assert body["results"][0]["name"] == first


def test_search_reports_an_unavailable_catalog_as_502(client, monkeypatch):
    patch_catalog(monkeypatch, error=RuntimeError("no catalog"))
    assert client.get("/api/skills/library/search?q=x").status_code == 502


# --- suggest ------------------------------------------------------------------


@pytest.fixture()
def suggest_env(monkeypatch):
    """Stub the ranking layer and the shared registry; record what they were given."""
    seen: dict = {"rank": [], "suggest": [], "index": []}

    def fake_index(home_arg, **kw):
        seen["index"].append(home_arg)
        return FakeIndex()

    def fake_rank(q, **kw):
        seen["rank"].append({"q": q, **kw})
        return [LibraryHit(id="alpha", name="alpha", description="d", score=0.7)]

    def fake_suggest(q, **kw):
        seen["suggest"].append({"q": q, **kw})
        return LibraryHit(id="beta", name="beta", description="d", score=0.9)

    monkeypatch.setattr("remedy.skills.library.suggest.build_library_index", fake_index)
    monkeypatch.setattr("remedy.skills.library.suggest.rank_library_skills", fake_rank)
    monkeypatch.setattr("remedy.skills.library.suggest.suggest_library_skill", fake_suggest)
    return seen


def patch_registry(monkeypatch, *, by_name=None, ranked=None, error=None):
    class Reg:
        def __init__(self) -> None:
            if by_name is not None:
                self._by_name = by_name

        def match_skills(self, q, limit=3):
            return ranked

    def fake():
        if error is not None:
            raise error
        return Reg()

    monkeypatch.setattr("remedy.skills.shared.get_shared_registry", fake)


def test_suggest_ranks_without_marking_by_default(client, suggest_env, monkeypatch):
    patch_registry(monkeypatch, by_name={})
    body = client.get("/api/skills/library/suggest?q=write a deploy pipeline").json()
    assert body["suggestion"]["id"] == "alpha"
    assert [r["id"] for r in body["results"]] == ["alpha"]
    assert body["source"] == "cache"
    assert body["index_size"] == 3
    assert suggest_env["rank"][0]["mark_suppressed"] is False
    assert suggest_env["suggest"] == []


def test_the_mark_form_returns_a_single_suggestion_and_no_result_list(
    client, suggest_env, monkeypatch
):
    """The two branches return different shapes — clients must not expect `results`."""
    patch_registry(monkeypatch, by_name={})
    body = client.get("/api/skills/library/suggest?q=deploy&mark=true").json()
    assert body["suggestion"]["id"] == "beta"
    assert "results" not in body
    assert suggest_env["suggest"][0]["mark_suggested"] is True
    assert suggest_env["suggest"][0]["intent"] == "tool"


def test_no_hits_yields_a_null_suggestion_not_a_404(client, monkeypatch, suggest_env):
    patch_registry(monkeypatch, by_name={})
    monkeypatch.setattr("remedy.skills.library.suggest.rank_library_skills", lambda q, **k: [])
    body = client.get("/api/skills/library/suggest?q=nothing matches this").json()
    assert body["suggestion"] is None
    assert body["results"] == []


def test_installed_skill_names_are_passed_to_the_ranker(client, suggest_env, monkeypatch):
    patch_registry(monkeypatch, by_name={"alpha": 1, "beta": 2})
    client.get("/api/skills/library/suggest?q=some longer query")
    assert suggest_env["rank"][0]["installed_names"] == {"alpha", "beta"}


def test_a_strong_installed_match_is_forwarded_as_the_cover_score(
    client, suggest_env, monkeypatch
):
    patch_registry(monkeypatch, by_name={}, ranked=[("alpha", 0.91)])
    client.get("/api/skills/library/suggest?q=deploy&mark=true")
    assert suggest_env["suggest"][0]["installed_top_score"] == pytest.approx(0.91)


def test_an_empty_query_never_asks_the_registry_for_a_cover_score(
    client, suggest_env, monkeypatch
):
    patch_registry(monkeypatch, by_name={}, ranked=[("alpha", 0.91)])
    client.get("/api/skills/library/suggest?mark=true")
    assert suggest_env["suggest"][0]["installed_top_score"] is None


def test_a_broken_registry_degrades_to_no_installed_context(client, suggest_env, monkeypatch):
    """Suggestion is advisory; a registry blow-up must not take the endpoint down."""
    patch_registry(monkeypatch, error=RuntimeError("registry on fire"))
    r = client.get("/api/skills/library/suggest?q=some longer query")
    assert r.status_code == 200
    assert suggest_env["rank"][0]["installed_names"] == set()


@pytest.mark.parametrize(("sent", "forwarded"), [("", None), ("s-1", "s-1")])
def test_a_blank_session_id_is_normalised_to_none(
    client, suggest_env, monkeypatch, sent, forwarded
):
    patch_registry(monkeypatch, by_name={})
    client.get(f"/api/skills/library/suggest?q=some longer query&session_id={sent}")
    assert suggest_env["rank"][0]["session_id"] == forwarded


def test_a_failing_index_build_is_not_swallowed(raw_client, monkeypatch):
    """It is outside the try/except — a broken cache must surface, not fake an answer."""
    patch_registry(monkeypatch, by_name={})
    monkeypatch.setattr(
        "remedy.skills.library.suggest.build_library_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("cache unreadable")),
    )
    assert raw_client.get("/api/skills/library/suggest?q=x").status_code == 500


# --- suggest/dismiss ----------------------------------------------------------


def test_dismissing_a_suggestion_suppresses_it_for_that_session(client):
    try:
        r = client.post(
            "/api/skills/library/suggest/dismiss",
            json={"skill_id": "alpha", "session_id": "s-9"},
        )
        assert r.json() == {"ok": True, "skill_id": "alpha"}
        assert is_suppressed("s-9", "alpha")
        assert not is_suppressed("other", "alpha")
    finally:
        clear_session_suppress("s-9")


def test_dismissing_without_a_session_id_lands_in_the_default_bucket(client):
    try:
        client.post("/api/skills/library/suggest/dismiss", json={"skill_id": "gamma"})
        assert is_suppressed("_default", "gamma")
    finally:
        clear_session_suppress("_default")


def test_a_dismiss_without_a_skill_id_is_rejected_by_the_schema(client):
    assert client.post("/api/skills/library/suggest/dismiss", json={}).status_code == 422


# --- install ------------------------------------------------------------------


def patch_install(monkeypatch, *, result=None, error: Exception | None = None) -> list[dict]:
    calls: list[dict] = []

    async def fake(skill_id, **kwargs):
        calls.append({"skill_id": skill_id, **kwargs})
        if error is not None:
            raise error
        return result or {"status": "installed"}

    monkeypatch.setattr("remedy.skills.library.install.install_skill_from_catalog", fake)
    return calls


def test_a_successful_install_is_returned_untouched(client, monkeypatch):
    patch_install(monkeypatch, result={"status": "installed", "quarantine": True})
    r = client.post("/api/skills/library/install", json={"skill_id": "alpha"})
    assert r.status_code == 200
    assert r.json() == {"status": "installed", "quarantine": True}


def test_install_defaults_to_no_version_and_no_force(client, monkeypatch, home):
    """Force means "delete what is there"; it must never be on unless asked for."""
    calls = patch_install(monkeypatch)
    client.post("/api/skills/library/install", json={"skill_id": "alpha"})
    assert calls[0]["version"] is None
    assert calls[0]["force"] is False
    assert calls[0]["home"] == home


def test_install_forwards_an_explicit_version_and_force(client, monkeypatch):
    calls = patch_install(monkeypatch)
    client.post(
        "/api/skills/library/install",
        json={"skill_id": "alpha", "version": "2.1.0", "force": True},
    )
    assert calls[0]["version"] == "2.1.0"
    assert calls[0]["force"] is True


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LookupError("Skill not found in catalog: nope"), 404),
        (FileExistsError("already installed"), 409),
        (ValueError("Checksum mismatch"), 400),
        (RuntimeError("download failed"), 502),
        (OSError("disk full"), 502),
    ],
)
def test_each_install_failure_maps_to_its_own_status(client, monkeypatch, error, status):
    patch_install(monkeypatch, error=error)
    r = client.post("/api/skills/library/install", json={"skill_id": "alpha"})
    assert r.status_code == status
    assert r.json()["detail"] == str(error)


def test_an_install_body_without_a_skill_id_is_rejected(client):
    assert client.post("/api/skills/library/install", json={"force": True}).status_code == 422


# --- updates ------------------------------------------------------------------


def test_updates_compares_the_catalog_against_the_installed_skills_dir(
    client, monkeypatch, home
):
    patch_catalog(monkeypatch, result=catalog(entry()))
    seen: list = []

    def fake_updates(cat, *, skills_dir):
        seen.append(skills_dir)
        return [{"skill_id": "alpha", "available_version": "2.0.0"}]

    monkeypatch.setattr("remedy.skills.library.install.list_library_updates", fake_updates)
    r = client.get("/api/skills/library/updates")
    assert r.json() == {"updates": [{"skill_id": "alpha", "available_version": "2.0.0"}]}
    assert seen[0] == home / "skills"


def test_updates_reports_an_unreachable_catalog_as_502(client, monkeypatch):
    patch_catalog(monkeypatch, error=RuntimeError("offline"))
    assert client.get("/api/skills/library/updates").status_code == 502


def test_a_failure_while_diffing_versions_is_not_disguised_as_a_502(raw_client, monkeypatch):
    """Only the fetch is wrapped; a loader crash surfaces as a 500 (see NOTES)."""
    patch_catalog(monkeypatch, result=catalog(entry()))
    monkeypatch.setattr(
        "remedy.skills.library.install.list_library_updates",
        lambda cat, **k: (_ for _ in ()).throw(RuntimeError("bad SKILL.md")),
    )
    assert raw_client.get("/api/skills/library/updates").status_code == 500


# --- update/{skill_id} --------------------------------------------------------


def test_an_update_always_forces_and_never_pins_a_version(client, monkeypatch):
    calls = patch_install(monkeypatch, result={"status": "installed", "replaced": True})
    r = client.post("/api/skills/library/update/alpha")
    assert r.status_code == 200
    assert calls[0]["skill_id"] == "alpha"
    assert calls[0]["force"] is True
    assert "version" not in calls[0]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LookupError("gone from catalog"), 404),
        (ValueError("Checksum mismatch"), 400),
        (RuntimeError("download failed"), 502),
        # No FileExistsError branch here — unlike /install it becomes a 502.
        (FileExistsError("already installed"), 502),
    ],
)
def test_each_update_failure_maps_to_its_own_status(client, monkeypatch, error, status):
    patch_install(monkeypatch, error=error)
    assert client.post("/api/skills/library/update/alpha").status_code == status


# --- submit -------------------------------------------------------------------

GOOD_SKILL_MD = """---
name: tidy-notes
description: Tidies up loose meeting notes into a short summary.
version: 1.2.0
author: someone
tags: [notes]
---

# Tidy notes

Read the notes, group them by topic, and write a short summary for each topic.
"""


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def submit(client, data: bytes, *, metadata: str | None = None, filename="skill.zip"):
    url = "/api/skills/library/submit"
    if metadata is not None:
        return client.post(
            url, params={"metadata": metadata}, files={"skill_zip": (filename, data)}
        )
    return client.post(url, files={"skill_zip": (filename, data)})


def test_a_well_formed_skill_zip_is_validated_but_never_installed(client):
    body = submit(client, make_zip({"tidy-notes/SKILL.md": GOOD_SKILL_MD})).json()
    assert body["status"] == "validated"
    assert body["skill_name"] == "tidy-notes"
    assert body["version"] == "1.2.0"
    assert body["author"] == "someone"
    assert "remedy-skills" in body["repository"]
    assert "feat/tidy-notes-v1.2.0" in body["instructions"]


def test_the_skill_md_may_sit_at_any_depth_in_the_zip(client):
    data = make_zip({"pack/nested/deeper/tidy-notes/SKILL.md": GOOD_SKILL_MD})
    assert submit(client, data).json()["skill_name"] == "tidy-notes"


def test_a_zip_with_no_skill_md_is_refused(client):
    r = submit(client, make_zip({"readme.txt": "hello there, nothing to see"}))
    assert r.status_code == 400
    assert r.json()["detail"] == "No SKILL.md in zip"


@pytest.mark.parametrize("data", [b"", b"short", b"x" * 31])
def test_a_payload_too_small_to_be_a_zip_is_refused_before_parsing(client, data):
    r = submit(client, data)
    assert r.status_code == 400
    assert r.json()["detail"] == "Empty or invalid zip"


def test_bytes_that_are_not_a_zip_at_all_come_back_as_400(client):
    r = submit(client, b"n" * 200)
    assert r.status_code == 400
    assert "not a zip" in r.json()["detail"].lower()


def test_a_zip_slip_member_is_blocked_rather_than_written(client):
    """`../evil.txt` must never escape the temp extract dir."""
    r = submit(client, make_zip({"../evil.txt": "pwned", "s/SKILL.md": GOOD_SKILL_MD}))
    assert r.status_code == 400
    assert "Zip Slip" in r.json()["detail"]


@pytest.mark.parametrize("banned", ["eval(", "exec(", "pickle.", "shell=True"])
def test_a_dangerous_pattern_in_any_bundled_file_fails_the_submission(client, banned):
    data = make_zip(
        {
            "s/SKILL.md": GOOD_SKILL_MD,
            "s/scripts/run.py": f"import subprocess\nsubprocess.run('ls', {banned})\n",
        }
    )
    r = submit(client, data)
    assert r.status_code == 400
    errors = r.json()["detail"]["errors"]
    assert any(banned in e for e in errors)


def test_the_pattern_scan_also_reads_extensionless_files(client):
    """A payload named `Makefile` is exactly where you would hide `shell=True`."""
    data = make_zip({"s/SKILL.md": GOOD_SKILL_MD, "s/Makefile": "run: shell=True\n"})
    assert submit(client, data).status_code == 400


def test_a_binary_extension_is_not_scanned(client):
    """Documents the scan's blind spot: only text-ish suffixes are read."""
    data = make_zip({"s/SKILL.md": GOOD_SKILL_MD, "s/blob.bin": "eval(1)"})
    assert submit(client, data).status_code == 200


def test_a_skill_missing_its_description_is_rejected_with_field_errors(client):
    md = "---\nname: tiny\ndescription: short\nversion: 1.0.0\n---\n\nbody text here\n"
    r = submit(client, make_zip({"s/SKILL.md": md}))
    assert r.status_code == 400
    assert any("Description" in e for e in r.json()["detail"]["errors"])


def test_a_skill_md_with_no_name_field_is_a_400_not_a_500(client):
    md = "---\ndescription: A description that is plenty long enough.\n---\n\nbody\n"
    r = submit(client, make_zip({"s/SKILL.md": md}))
    assert r.status_code == 400
    assert "name" in r.json()["detail"]


@pytest.mark.parametrize("name", ["../escape", "has space", "a/b", ".hidden", ""])
def test_a_skill_name_that_is_really_a_path_is_refused(client, name):
    md = (
        f"---\nname: '{name}'\ndescription: A description that is plenty long enough.\n"
        "version: 1.0.0\n---\n\nSome instructions that are long enough to pass.\n"
    )
    r = submit(client, make_zip({"s/SKILL.md": md}))
    assert r.status_code == 400
    detail = r.json()["detail"]
    # Empty names trip the metadata validator first; the rest trip the name regex.
    assert detail == "Invalid skill name" or "name" in json.dumps(detail).lower()


def test_metadata_may_override_the_declared_author(client):
    body = submit(
        client,
        make_zip({"s/SKILL.md": GOOD_SKILL_MD}),
        metadata=json.dumps({"author": "override"}),
    ).json()
    assert body["author"] == "override"


def test_metadata_falls_back_to_the_manifest_author_when_blank(client):
    body = submit(
        client, make_zip({"s/SKILL.md": GOOD_SKILL_MD}), metadata=json.dumps({"author": ""})
    ).json()
    assert body["author"] == "someone"


def test_unparseable_metadata_json_is_refused_before_the_zip_is_touched(client):
    r = submit(client, make_zip({"s/SKILL.md": GOOD_SKILL_MD}), metadata="{not json")
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Invalid metadata JSON:")


@pytest.mark.parametrize("meta", ["[1, 2]", '"a string"', "null", "7"])
def test_metadata_that_is_valid_json_but_not_an_object_is_still_a_400(client, meta):
    """It gets as far as `raw_meta.get(...)`; the point is it is not a 500."""
    r = submit(client, make_zip({"s/SKILL.md": GOOD_SKILL_MD}), metadata=meta)
    assert r.status_code == 400


def test_metadata_sent_as_a_form_field_is_silently_ignored(client):
    """It is declared as a query parameter — see NOTES; this pins current behaviour."""
    r = client.post(
        "/api/skills/library/submit",
        files={"skill_zip": ("s.zip", make_zip({"s/SKILL.md": GOOD_SKILL_MD}))},
        data={"metadata": json.dumps({"author": "override"})},
    )
    assert r.json()["author"] == "someone"


def test_a_submission_with_no_file_is_rejected_by_the_schema(client):
    assert client.post("/api/skills/library/submit").status_code == 422


def test_warnings_are_surfaced_rather_than_hidden(client):
    """A skill with no tags still passes, but the submitter should be told."""
    md = (
        "---\nname: bare\ndescription: A description that is plenty long enough.\n"
        "version: 1.0.0\n---\n\nSome instructions that are long enough to pass.\n"
    )
    body = submit(client, make_zip({"s/SKILL.md": md})).json()
    assert any("tags" in w for w in body["warnings"])


def test_the_temp_extract_directory_is_always_removed(client, monkeypatch):
    made: list[str] = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        p = real(*a, **k)
        if str(k.get("prefix", "")).startswith("remedy-lib-submit-"):
            made.append(p)
        return p

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    submit(client, make_zip({"s/SKILL.md": GOOD_SKILL_MD}))
    submit(client, b"n" * 200)  # failure path must clean up too
    assert made
    assert not any(Path(p).exists() for p in made)


def test_a_submission_does_not_leak_a_sandbox_directory(client, monkeypatch):
    """SkillValidator() used to build a SkillExecutor eagerly, and its
    constructor mkdtemps a sandbox nobody removes — one orphaned directory per
    request, even though submit only calls validate_metadata(), which never
    runs a script. The executor is built on first use now."""
    leaked: list[str] = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        p = real(*a, **k)
        if str(k.get("prefix", "")).startswith("remedy_exec_"):
            leaked.append(p)
        return p

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    try:
        for _ in range(3):
            assert submit(client, make_zip({"s/SKILL.md": GOOD_SKILL_MD})).status_code == 200
        assert leaked == [], "a sandbox was created for a metadata-only check"
    finally:
        for p in leaked:
            with contextlib.suppress(OSError):
                Path(p).rmdir()

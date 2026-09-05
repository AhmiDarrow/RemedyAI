"""Skills Library: catalog verify, allowlist, local install into quarantine."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from remedy.skills.library.catalog import SkillsCatalog, get_skills_catalog, search_catalog
from remedy.skills.library.install import install_skill_from_catalog
from remedy.skills.library.keys import (
    CATALOG_PUBLIC_KEY_B64,
    DEFAULT_CATALOG_URL,
    RAW_CATALOG_URL,
)
from remedy.skills.library.security import (
    is_allowed_catalog_url,
    is_allowed_download_url,
    is_safe_skill_name,
    verify_catalog_signature,
)

REPO = Path(__file__).resolve().parents[1]
COMMUNITY = REPO / "community" / "remedy-skills"


def test_production_pubkey_is_not_empty():
    raw = base64.b64decode(CATALOG_PUBLIC_KEY_B64)
    assert len(raw) == 32


def test_verify_catalog_signature_ephemeral_key():
    """Use a throwaway keypair — production seed must never live in the tree."""
    sk = SigningKey.generate()
    pub = base64.b64encode(bytes(sk.verify_key)).decode()
    msg = b'{"version":"1","skills":[]}'
    sig = base64.b64encode(sk.sign(msg).signature).decode()
    verify_catalog_signature(msg, sig, public_key_b64=pub)
    with pytest.raises(ValueError):
        verify_catalog_signature(msg + b"x", sig, public_key_b64=pub)


def test_allowlist_urls():
    assert is_allowed_download_url("local:hello-library")
    assert is_allowed_download_url(
        "https://github.com/AhmiDarrow/remedy-skills/releases/download/v1.0.0/x.zip"
    )
    assert not is_allowed_download_url("https://evil.example/x.zip")
    assert not is_allowed_download_url("http://github.com/AhmiDarrow/remedy-skills/x")
    assert not is_allowed_download_url("local:../etc/passwd")
    # Substring trap should fail (not a strict /releases/download/ path)
    assert not is_allowed_download_url(
        "https://github.com/evil/repo/blob/main/AhmiDarrow/remedy-skills/releases/download/v1/x.zip"
    )
    assert not is_allowed_download_url(
        "https://github.com/AhmiDarrow/remedy-skills/tree/main/skills"
    )
    # CDN hosts must not appear as catalog initial URLs (redirect hop only).
    assert not is_allowed_download_url(
        "https://objects.githubusercontent.com/github-production-release-asset/1/x"
    )
    assert is_allowed_download_url(
        "https://objects.githubusercontent.com/github-production-release-asset/1/x",
        allow_cdn_redirect=True,
    )


def test_catalog_url_allowlist():
    """S-SKILL-01: catalog fetch hosts match zip allowlist (+ raw GH for this repo)."""
    assert is_allowed_catalog_url(DEFAULT_CATALOG_URL)
    assert is_allowed_catalog_url(DEFAULT_CATALOG_URL + ".sig")
    assert is_allowed_catalog_url(RAW_CATALOG_URL)
    assert is_allowed_catalog_url(RAW_CATALOG_URL + ".sig")
    assert not is_allowed_catalog_url("https://evil.example/catalog.json")
    assert not is_allowed_catalog_url(
        "https://raw.githubusercontent.com/evil/repo/main/catalog.json"
    )
    assert not is_allowed_catalog_url("http://127.0.0.1:1/missing")


def test_safe_skill_name():
    assert is_safe_skill_name("godot-game-engine")
    assert not is_safe_skill_name("../etc")
    assert not is_safe_skill_name("a/b")
    assert not is_safe_skill_name("")


@pytest.mark.asyncio
async def test_local_signed_catalog_loads():
    cat_path = COMMUNITY / "catalog.json"
    sig_path = COMMUNITY / "catalog.json.sig"
    if not cat_path.is_file() or not sig_path.is_file():
        pytest.skip("community catalog not built")
    cat = await get_skills_catalog(
        refresh=True,
        allow_local_fallback=True,
        catalog_url="http://127.0.0.1:1/missing",  # force remote fail
        sig_url="http://127.0.0.1:1/missing.sig",
    )
    assert cat.source == "local"
    assert any(s.id == "hello-library" for s in cat.skills)
    found = search_catalog(cat, q="hello")
    assert found and found[0].id == "hello-library"


@pytest.mark.asyncio
async def test_install_local_hello_quarantine(tmp_path: Path):
    cat_path = COMMUNITY / "catalog.json"
    if not cat_path.is_file():
        pytest.skip("community catalog not built")
    raw = json.loads(cat_path.read_text(encoding="utf-8"))
    # Use local: URLs for dogfood install without network
    for s in raw.get("skills") or []:
        s["download_url"] = f"local:{s['id']}"
        # Recompute checksum for local zip so install verifies
    cat = SkillsCatalog.model_validate(raw)
    cat.source = "local"

    # Patch entries with correct local zip checksums via resolve path
    import hashlib

    from remedy.skills.library.install import _zip_from_local_skill

    for entry in cat.skills:
        if entry.id == "hello-library":
            z = _zip_from_local_skill("hello-library")
            entry.checksum = f"sha256:{hashlib.sha256(z).hexdigest()}"
            entry.download_url = "local:hello-library"
            break

    class FakeReg:
        def __init__(self):
            self.registered = []

        def register(self, skill):
            self.registered.append(skill)

        def remove(self, name):
            self.registered = [s for s in self.registered if s.manifest.name != name]

    class FakeRuntime:
        def __init__(self):
            self.skills = FakeReg()
            self.config = type("C", (), {"home_dir": str(tmp_path)})()

    rt = FakeRuntime()
    result = await install_skill_from_catalog(
        "hello-library",
        runtime=rt,
        home=tmp_path,
        catalog=cat,
    )
    assert result["quarantine"] is True
    assert (tmp_path / "skills" / "hello-library" / "SKILL.md").is_file()
    assert rt.skills.registered
    meta = rt.skills.registered[0].manifest.metadata or {}
    assert meta.get("quarantine") is True
    assert meta.get("source") == "library"

    # force replace must keep canonical name (not name-imported)
    result2 = await install_skill_from_catalog(
        "hello-library",
        runtime=rt,
        home=tmp_path,
        catalog=cat,
        force=True,
    )
    assert result2["replaced"] is True
    assert (tmp_path / "skills" / "hello-library").is_dir()
    assert not (tmp_path / "skills" / "hello-library-imported").exists()

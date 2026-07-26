"""Skills Library: catalog verify, allowlist, local install into quarantine."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from remedy.skills.library.catalog import SkillsCatalog, get_skills_catalog, search_catalog
from remedy.skills.library.install import install_skill_from_catalog
from remedy.skills.library.keys import CATALOG_PUBLIC_KEY_B64
from remedy.skills.library.security import is_allowed_download_url, verify_catalog_signature

REPO = Path(__file__).resolve().parents[1]
COMMUNITY = REPO / "community" / "remedy-skills"


def test_public_key_matches_dev_signing_seed():
    # Seed used to sign community catalog in development
    seed = base64.b64decode("wjxrCLQL+CLNFniTWTTTBzRkwx44NdVYc/Yq0OqqUi4=")
    sk = SigningKey(seed)
    pub = base64.b64encode(bytes(sk.verify_key)).decode()
    assert pub == CATALOG_PUBLIC_KEY_B64


def test_verify_catalog_signature_roundtrip():
    seed = base64.b64decode("wjxrCLQL+CLNFniTWTTTBzRkwx44NdVYc/Yq0OqqUi4=")
    sk = SigningKey(seed)
    msg = b'{"version":"1","skills":[]}'
    sig = base64.b64encode(sk.sign(msg).signature).decode()
    verify_catalog_signature(msg, sig)
    with pytest.raises(ValueError):
        verify_catalog_signature(msg + b"x", sig)


def test_allowlist_urls():
    assert is_allowed_download_url("local:hello-library")
    assert is_allowed_download_url(
        "https://github.com/AhmiDarrow/remedy-skills/releases/download/v1.0.0/x.zip"
    )
    assert not is_allowed_download_url("https://evil.example/x.zip")
    assert not is_allowed_download_url("http://github.com/AhmiDarrow/remedy-skills/x")
    assert not is_allowed_download_url("local:../etc/passwd")


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
    cat = SkillsCatalog.model_validate(raw)
    cat.source = "local"

    class FakeReg:
        def __init__(self):
            self.registered = []

        def register(self, skill):
            self.registered.append(skill)

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

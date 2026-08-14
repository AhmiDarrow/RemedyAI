"""Fetch, verify, cache Skills Library catalog."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from remedy.skills.library.keys import (
    CATALOG_PUBLIC_KEY_B64,
    DEFAULT_CATALOG_SIG_URL,
    DEFAULT_CATALOG_URL,
)
from remedy.skills.library.security import (
    is_allowed_catalog_url,
    is_allowed_download_url,
    skills_dev_mode,
    verify_catalog_signature,
)

logger = logging.getLogger(__name__)

CACHE_MAX_AGE_S = 86_400  # 24h


class SkillCatalogEntry(BaseModel):
    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = "community"
    author_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    download_url: str
    size_bytes: int = 0
    checksum: str  # sha256:...
    requires: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    rating: float = 0.0
    installs: int = 0
    reviews_count: int = 0
    updated_at: str = ""
    published_at: str = ""
    compatible_remedy: list[str] = Field(default_factory=lambda: [">=0.15.0"])
    security_flags: list[str] = Field(default_factory=list)
    status: str = "published"


class SkillsCatalog(BaseModel):
    version: str = "1"
    generated_at: str = ""
    repository: str = "AhmiDarrow/remedy-skills"
    skills: list[SkillCatalogEntry] = Field(default_factory=list)
    source: str = "remote"  # remote | cache | local


def _home_cache_dir(home: Path | None = None) -> Path:
    if home is None:
        home = Path.home() / ".remedy"
    return Path(home).expanduser() / "cache" / "skills"


def _repo_local_catalog() -> Path | None:
    """Monorepo community/remedy-skills/catalog.json if present."""
    # src/remedy/skills/library/catalog.py -> parents[4] = repo root
    try:
        root = Path(__file__).resolve().parents[4]
    except IndexError:
        return None
    p = root / "community" / "remedy-skills" / "catalog.json"
    return p if p.is_file() else None


def _load_signed_local(catalog_path: Path, *, public_key_b64: str | None = None) -> SkillsCatalog:
    data = catalog_path.read_bytes()
    sig_path = Path(str(catalog_path) + ".sig")
    if not sig_path.is_file():
        raise ValueError(f"Missing signature file: {sig_path}")
    sig = sig_path.read_text(encoding="utf-8").strip()
    verify_catalog_signature(data, sig, public_key_b64=public_key_b64)
    raw = json.loads(data.decode("utf-8"))
    cat = SkillsCatalog.model_validate(raw)
    cat.source = "local"
    return cat


async def _http_get(
    url: str,
    *,
    timeout_s: float = 15.0,
    max_bytes: int = 8 * 1024 * 1024,
) -> bytes:
    import aiohttp

    async with aiohttp.ClientSession() as session, session.get(
        url, timeout=aiohttp.ClientTimeout(total=timeout_s)
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        final = str(resp.url)
        if not is_allowed_catalog_url(final) and not is_allowed_download_url(
            final, allow_cdn_redirect=True
        ):
            raise RuntimeError(f"Catalog final URL not allowlisted: {final[:160]}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"Response exceeds size limit ({max_bytes} bytes) for {url}")
            chunks.append(chunk)
        return b"".join(chunks)


async def get_skills_catalog(
    *,
    refresh: bool = False,
    home: Path | None = None,
    catalog_url: str | None = None,
    sig_url: str | None = None,
    public_key_b64: str | None = None,
    allow_local_fallback: bool = True,
) -> SkillsCatalog:
    """Load verified catalog (cache → remote → optional monorepo local)."""
    cache_dir = _home_cache_dir(home)
    cache_file = cache_dir / "catalog.json"
    cache_sig = cache_dir / "catalog.json.sig"
    dev = skills_dev_mode()
    # Non-default verify keys only with REMEDY_SKILLS_DEV (S-SKILL-01).
    requested_pub = (
        public_key_b64
        or os.environ.get("REMEDY_SKILLS_CATALOG_PUBKEY")
        or CATALOG_PUBLIC_KEY_B64
    )
    if requested_pub != CATALOG_PUBLIC_KEY_B64 and not dev:
        logger.warning(
            "Ignoring non-default REMEDY_SKILLS_CATALOG_PUBKEY "
            "(set REMEDY_SKILLS_DEV=1 for dogfood keys)"
        )
        pubkey = CATALOG_PUBLIC_KEY_B64
    else:
        pubkey = requested_pub

    if not refresh and cache_file.is_file() and cache_sig.is_file():
        age = datetime.now(UTC).timestamp() - cache_file.stat().st_mtime
        if age < CACHE_MAX_AGE_S:
            try:
                data = cache_file.read_bytes()
                sig = cache_sig.read_text(encoding="utf-8").strip()
                verify_catalog_signature(data, sig, public_key_b64=pubkey)
                cat = SkillsCatalog.model_validate(json.loads(data.decode("utf-8")))
                cat.source = "cache"
                return cat
            except Exception as e:
                logger.debug("skills catalog cache invalid: %s", e)

    url = catalog_url or os.environ.get("REMEDY_SKILLS_CATALOG_URL") or DEFAULT_CATALOG_URL
    surl = sig_url or os.environ.get("REMEDY_SKILLS_CATALOG_SIG_URL") or DEFAULT_CATALOG_SIG_URL

    try:
        # Fail closed: catalog fetch hosts match zip allowlist (S-SKILL-01).
        if not dev and not (
            is_allowed_catalog_url(url) and is_allowed_catalog_url(surl)
        ):
            raise RuntimeError(
                "Skills catalog URL not allowlisted "
                f"(url={url[:120]!r}); set REMEDY_SKILLS_DEV=1 for dogfood"
            )
        data = await _http_get(url)
        sig_bytes = await _http_get(surl)
        sig = sig_bytes.decode("utf-8").strip()
        verify_catalog_signature(data, sig, public_key_b64=pubkey)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Atomic-ish cache write (temp + replace)
        tmp_c = cache_dir / "catalog.json.tmp"
        tmp_s = cache_dir / "catalog.json.sig.tmp"
        tmp_c.write_bytes(data)
        tmp_s.write_text(sig + "\n", encoding="utf-8")
        tmp_c.replace(cache_file)
        tmp_s.replace(cache_sig)
        if pubkey != CATALOG_PUBLIC_KEY_B64 or url != DEFAULT_CATALOG_URL:
            logger.warning(
                "Skills catalog using non-default verify key and/or URL "
                "(pubkey override=%s url=%s)",
                pubkey != CATALOG_PUBLIC_KEY_B64,
                url,
            )
        cat = SkillsCatalog.model_validate(json.loads(data.decode("utf-8")))
        cat.source = "remote"
        return cat
    except Exception as e:
        logger.info("remote skills catalog unavailable: %s", e)
        if not allow_local_fallback:
            raise

    local = _repo_local_catalog()
    if local is not None:
        cat = _load_signed_local(local, public_key_b64=pubkey)
        return cat

    raise RuntimeError(
        "Could not load Skills Library catalog (remote failed and no signed local catalog)."
    )


def search_catalog(
    catalog: SkillsCatalog,
    *,
    q: str = "",
    tags: list[str] | None = None,
    author: str | None = None,
    sort_by: str = "name",
) -> list[SkillCatalogEntry]:
    results = list(catalog.skills)
    if q:
        ql = q.lower()
        results = [
            s
            for s in results
            if ql in s.name.lower()
            or ql in s.description.lower()
            or any(ql in t.lower() for t in s.tags)
        ]
    if tags:
        want = {t.lower() for t in tags}
        results = [s for s in results if want & {t.lower() for t in s.tags}]
    if author:
        al = author.lower()
        results = [s for s in results if s.author.lower() == al]

    if sort_by == "rating":
        results.sort(key=lambda s: s.rating, reverse=True)
    elif sort_by == "updated_at":
        results.sort(key=lambda s: s.updated_at, reverse=True)
    elif sort_by == "installs":
        results.sort(key=lambda s: s.installs, reverse=True)
    else:
        results.sort(key=lambda s: s.name.lower())
    return results


def catalog_to_public_dict(catalog: SkillsCatalog) -> dict[str, Any]:
    return catalog.model_dump()

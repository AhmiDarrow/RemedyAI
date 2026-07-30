"""Catalog signature verify + download URL allowlist."""

from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from remedy.skills.library.keys import CATALOG_PUBLIC_KEY_B64, LIBRARY_REPO

# Allowed remote download hosts for skill zips
_ALLOWED_HOSTS = frozenset(
    {
        "github.com",
        "www.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)

# Safe skill directory / catalog id name
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def is_safe_skill_name(name: str) -> bool:
    """True if name is a single safe path segment (no traversal)."""
    n = (name or "").strip()
    if not n or "/" in n or "\\" in n or ".." in n:
        return False
    return bool(SKILL_NAME_RE.fullmatch(n))


def verify_catalog_signature(
    catalog_bytes: bytes,
    signature_b64: str,
    *,
    public_key_b64: str | None = None,
) -> None:
    """Verify Ed25519 signature over exact catalog file bytes. Raises ValueError if invalid."""
    pub = (public_key_b64 or CATALOG_PUBLIC_KEY_B64).strip()
    try:
        vk = VerifyKey(base64.b64decode(pub))
        sig = base64.b64decode(signature_b64.strip())
    except Exception as e:
        raise ValueError(f"Invalid key or signature encoding: {e}") from e
    try:
        vk.verify(catalog_bytes, sig)
    except BadSignatureError as e:
        raise ValueError("Catalog signature verification failed") from e


def skills_dev_mode() -> bool:
    """True when REMEDY_SKILLS_DEV opts into non-default catalog URL/key (dogfood)."""
    import os

    return str(os.environ.get("REMEDY_SKILLS_DEV", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_allowed_download_url(url: str, *, allow_cdn_redirect: bool = False) -> bool:
    """True if URL is local dogfood or an allowed GitHub release asset for remedy-skills.

    Catalog / initial download URLs must be github.com release paths for this repo.
    GitHub CDN hosts are allowed only as the *final* hop after a redirect from that
    release URL (``allow_cdn_redirect=True``), never as a catalog download_url.
    """
    u = (url or "").strip()
    if not u:
        return False
    if u.startswith("local:"):
        # local:skill-id — resolved against monorepo community pack
        sid = u[6:].strip()
        return is_safe_skill_name(sid)

    if not u.lower().startswith("https://"):
        return False
    parsed = urlparse(u)
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        return False
    path = parsed.path or ""

    # Official release download pages on github.com
    if host in ("github.com", "www.github.com"):
        # Strict: /AhmiDarrow/remedy-skills/releases/download/<tag>/<file>
        prefix = f"/{LIBRARY_REPO}/releases/download/"
        if not path.startswith(prefix):
            return False
        rest = path[len(prefix) :]
        # tag/file — both single segments, no traversal
        parts = [p for p in rest.split("/") if p]
        if len(parts) != 2:
            return False
        tag, filename = parts
        if ".." in tag or ".." in filename:
            return False
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9._+-]{1,200}", filename))

    # GitHub release asset CDN — final hop only (never catalog initial URL).
    if host in ("objects.githubusercontent.com", "release-assets.githubusercontent.com"):
        if not allow_cdn_redirect:
            return False
        if ".." in path:
            return False
        cleaned = path.strip("/")
        # Require production-release path shape when present; still size-bound.
        if "github-production-release-asset" not in cleaned and "release" not in cleaned:
            return False
        return bool(cleaned) and len(cleaned) <= 500

    return False


def is_allowed_catalog_url(url: str) -> bool:
    """True if *url* is a safe Skills Library catalog or catalog.sig location.

    Same host policy as skill zips (GitHub release assets for this repo), plus
    the explicit raw.githubusercontent.com fallback path for this library repo.
    Off-list URLs require ``REMEDY_SKILLS_DEV=1`` (checked by callers).
    """
    u = (url or "").strip()
    if not u:
        return False
    # Release assets (catalog.json / catalog.json.sig / zips share the shape).
    if is_allowed_download_url(u):
        return True
    if not u.lower().startswith("https://"):
        return False
    parsed = urlparse(u)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    # Official raw main catalog (keys.RAW_CATALOG_URL) — still this repo only.
    if host == "raw.githubusercontent.com":
        # /AhmiDarrow/remedy-skills/<ref>/catalog.json[.sig]
        prefix = f"/{LIBRARY_REPO}/"
        if not path.startswith(prefix):
            return False
        rest = path[len(prefix) :]
        parts = [p for p in rest.split("/") if p]
        if len(parts) != 2:
            return False
        ref, filename = parts
        if ".." in ref or ".." in filename:
            return False
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", ref):
            return False
        return filename in ("catalog.json", "catalog.json.sig")
    return False

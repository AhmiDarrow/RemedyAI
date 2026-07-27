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


def is_allowed_download_url(url: str) -> bool:
    """True if URL is local dogfood or an allowed GitHub release asset for remedy-skills."""
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

    # GitHub release asset CDN — only as final hop after github.com release URL.
    # Path is opaque (numeric IDs); require release-asset path shape, no path traversal.
    if host in ("objects.githubusercontent.com", "release-assets.githubusercontent.com"):
        if ".." in path:
            return False
        # Typical: /github-production-release-asset/... or similar under objects
        cleaned = path.strip("/")
        return bool(cleaned) and len(cleaned) <= 500

    return False

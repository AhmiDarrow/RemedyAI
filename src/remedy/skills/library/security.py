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
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", sid))

    if not u.lower().startswith("https://"):
        return False
    parsed = urlparse(u)
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        return False
    path = parsed.path or ""
    # github.com/AhmiDarrow/remedy-skills/releases/download/...
    if host in ("github.com", "www.github.com"):
        return f"/{LIBRARY_REPO}/releases/" in path or path.startswith(
            f"/{LIBRARY_REPO}/releases/"
        )
    # CDN assets often include repo in path or query — require path contains repo owner
    return "AhmiDarrow" in path or "remedy-skills" in path

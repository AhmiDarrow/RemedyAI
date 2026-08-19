"""Minimal RS256 JWT verify + JWKS cache (stdlib only).

Used by Teams Bot Framework inbound auth. Not on the local chat/computer hot path.
JWKS is cached aggressively so webhook verification is one modular exponentiation
after the first fetch (~1–2ms), not a network round-trip every message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# SHA-256 DigestInfo prefix (PKCS#1 v1.5)
_DIGESTINFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")

# Bot Framework / Azure AD OpenID metadata (JWKS discovery).
_DEFAULT_OPENID_URLS = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration",
    "https://login.microsoftonline.com/botframework.com/v2.0/.well-known/openid-configuration",
)

# Module-level JWKS cache: kid → (n_int, e_int)
_JWKS_LOCK = threading.Lock()
_JWKS: dict[str, tuple[int, int]] = {}
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL_S = 6 * 3600  # 6h — BF keys rotate rarely; fail-open refresh on kid miss
# Test / offline inject: map kid → (n, e) without network
_TEST_KEYS: dict[str, tuple[int, int]] = {}


def b64url_decode(data: str) -> bytes:
    s = (data or "").encode("ascii") if isinstance(data, str) else data
    pad = b"=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def clear_jwks_cache() -> None:
    """Test helper: drop cached network keys (keeps test injects)."""
    global _JWKS_FETCHED_AT
    with _JWKS_LOCK:
        _JWKS.clear()
        _JWKS_FETCHED_AT = 0.0


def inject_test_rsa_key(*, kid: str, n_b64: str, e_b64: str = "AQAB") -> None:
    """Register a public key for unit tests (no network)."""
    n = int.from_bytes(b64url_decode(n_b64), "big")
    e = int.from_bytes(b64url_decode(e_b64), "big")
    with _JWKS_LOCK:
        _TEST_KEYS[str(kid)] = (n, e)


def clear_test_rsa_keys() -> None:
    with _JWKS_LOCK:
        _TEST_KEYS.clear()


def _rsa_public_op(sig: bytes, n: int, e: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        # Allow missing leading zeros
        if len(sig) > k:
            return b""
        sig = sig.rjust(k, b"\x00")
    m = pow(int.from_bytes(sig, "big"), e, n)
    return m.to_bytes(k, "big")


def verify_rs256(token: str, *, n: int, e: int) -> bool:
    """Verify RS256 JWT signature with RSA public key (n, e)."""
    parts = (token or "").split(".")
    if len(parts) != 3:
        return False
    header_b64, payload_b64, sig_b64 = parts
    try:
        sig = b64url_decode(sig_b64)
    except Exception:
        return False
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    digest = hashlib.sha256(signing_input).digest()
    em = _rsa_public_op(sig, n, e)
    if not em:
        return False
    # PKCS#1 v1.5: 0x00 0x01 PS 0x00 DigestInfo
    if len(em) < 11 or em[0:2] != b"\x00\x01":
        return False
    try:
        sep = em.index(b"\x00", 2)
    except ValueError:
        return False
    ps = em[2:sep]
    if len(ps) < 8 or any(b != 0xFF for b in ps):
        return False
    digest_info = em[sep + 1 :]
    expected = _DIGESTINFO_SHA256 + digest
    # Exact match, constant time. Requiring the DigestInfo to be *exactly* the
    # tail is what rules out the classic e=3 forgery, where trailing garbage is
    # ignored; comparing in constant time is the standard on top of that.
    return hmac.compare_digest(digest_info, expected)


def decode_jwt_header(token: str) -> dict[str, Any] | None:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    try:
        data = json.loads(b64url_decode(parts[0]).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def decode_jwt_payload_unverified(token: str) -> dict[str, Any] | None:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    try:
        data = json.loads(b64url_decode(parts[1]).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


_JWKS_HOSTS = frozenset(
    {
        "login.botframework.com",
        "login.microsoftonline.com",
        "login.microsoft.com",
        "login.windows.net",
    }
)


def _jwks_url_allowed(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _JWKS_HOSTS:
        return True
    return host.endswith(".microsoftonline.com") or host.endswith(".windows.net")


def _http_get_json(url: str, *, timeout: float = 4.0) -> dict[str, Any] | None:
    if not _jwks_url_allowed(url):
        logger.warning("JWKS URL refused (host not allowlisted): %s", (url or "")[:80])
        return None
    try:
        from remedy.core.security import urlopen_no_redirect

        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "RemedyAI-TeamsJWT/1"},
            method="GET",
        )
        with urlopen_no_redirect(req, timeout=timeout) as resp:
            raw = resp.read(2 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("JWKS fetch failed %s: %s", url[:80], exc)
        return None


def _ingest_jwk_set(doc: dict[str, Any]) -> int:
    keys = doc.get("keys")
    if not isinstance(keys, list):
        return 0
    n_added = 0
    for jwk in keys:
        if not isinstance(jwk, dict):
            continue
        if str(jwk.get("kty") or "").upper() != "RSA":
            continue
        kid = str(jwk.get("kid") or "").strip()
        n_b64 = str(jwk.get("n") or "").strip()
        e_b64 = str(jwk.get("e") or "AQAB").strip()
        if not kid or not n_b64:
            continue
        try:
            n = int.from_bytes(b64url_decode(n_b64), "big")
            e = int.from_bytes(b64url_decode(e_b64), "big")
        except Exception:
            continue
        _JWKS[kid] = (n, e)
        n_added += 1
    return n_added


def refresh_jwks(*, force: bool = False, openid_urls: tuple[str, ...] | None = None) -> bool:
    """Fetch Bot Framework / Azure AD JWKS into the module cache.

    Returns True if at least one RSA key was loaded.
    """
    global _JWKS_FETCHED_AT
    now = time.time()
    with _JWKS_LOCK:
        if (
            not force
            and _JWKS
            and (now - _JWKS_FETCHED_AT) < _JWKS_TTL_S
        ):
            return True

    urls = openid_urls or _DEFAULT_OPENID_URLS
    jwks_uris: list[str] = []
    for meta_url in urls:
        meta = _http_get_json(meta_url)
        if not meta:
            continue
        juri = str(meta.get("jwks_uri") or "").strip()
        if _jwks_url_allowed(juri):
            jwks_uris.append(juri)
    # Also try well-known keys endpoints directly (fast path when meta is slow)
    jwks_uris.extend(
        [
            "https://login.botframework.com/v1/.well-known/keys",
            "https://login.microsoftonline.com/common/discovery/v2.0/keys",
        ]
    )
    seen: set[str] = set()
    total = 0
    with _JWKS_LOCK:
        for uri in jwks_uris:
            if uri in seen:
                continue
            seen.add(uri)
            doc = _http_get_json(uri)
            if doc:
                total += _ingest_jwk_set(doc)
        if total:
            _JWKS_FETCHED_AT = time.time()
            logger.info("Teams JWKS loaded: %d RSA keys", len(_JWKS))
            return True
    return bool(_JWKS)


def _lookup_key(kid: str) -> tuple[int, int] | None:
    with _JWKS_LOCK:
        if kid in _TEST_KEYS:
            return _TEST_KEYS[kid]
        if kid in _JWKS:
            return _JWKS[kid]
    return None


def verify_jwt_rs256_jwks(
    token: str,
    *,
    allow_network: bool = True,
) -> bool:
    """Verify JWT with RS256 using cached (or freshly fetched) JWKS.

    Network is only used when the kid is unknown or the cache is empty/expired.
    """
    header = decode_jwt_header(token)
    if not header:
        return False
    alg = str(header.get("alg") or "").upper()
    if alg != "RS256":
        # Bot Framework uses RS256; reject others fail-closed
        logger.warning("Teams JWT alg not RS256: %s", alg)
        return False
    kid = str(header.get("kid") or "").strip()
    if not kid:
        logger.warning("Teams JWT missing kid")
        return False

    key = _lookup_key(kid)
    if key is None and allow_network:
        refresh_jwks(force=False)
        key = _lookup_key(kid)
        if key is None:
            # kid miss after soft refresh — force once
            refresh_jwks(force=True)
            key = _lookup_key(kid)
    if key is None:
        logger.warning("Teams JWT kid not in JWKS: %s", kid[:40])
        return False
    n, e = key
    if not verify_rs256(token, n=n, e=e):
        logger.warning("Teams JWT RS256 signature invalid")
        return False
    return True

"""Embedded public key for Skills Library catalog verification.

Production private seed lives only in CI secret REMEDY_SKILLS_SIGNING_KEY
(and optionally ~/.remedy/auth/skills_catalog_signing_seed.b64 on maintainer machines).
Rotate by generating a new Ed25519 seed, updating this constant, re-signing the
public catalog, and updating the GitHub Actions secret.
"""

from __future__ import annotations

# Base64 of 32-byte Ed25519 verify key (rotated 2026-07-26 after audit)
CATALOG_PUBLIC_KEY_B64 = "Zecma7eUlNtKbQSnTeBJC+v9nQEmJ5OT6YTYq27GsLc="

# Official catalog locations — prefer signed *release* assets (immutable + verified).
# Bump tag when publishing a new library release.
LIBRARY_RELEASE_TAG = "v1.0.0"
LIBRARY_REPO = "AhmiDarrow/remedy-skills"
DEFAULT_CATALOG_URL = (
    f"https://github.com/{LIBRARY_REPO}/releases/download/"
    f"{LIBRARY_RELEASE_TAG}/catalog.json"
)
DEFAULT_CATALOG_SIG_URL = DEFAULT_CATALOG_URL + ".sig"
# Fallback raw main (may lag CDN); used only if explicitly configured
RAW_CATALOG_URL = (
    f"https://raw.githubusercontent.com/{LIBRARY_REPO}/main/catalog.json"
)

"""Embedded public key for Skills Library catalog verification.

Generated for remedy-skills Ed25519 (PyNaCl SigningKey seed).
Rotate by updating this constant and re-signing the public catalog.
"""

from __future__ import annotations

# Base64 of 32-byte Ed25519 verify key (matches REMEDY_SKILLS_SIGNING_KEY seed used in dev)
CATALOG_PUBLIC_KEY_B64 = "op2KL8LRMHmwjJfywQWgZthCcfwkEt/VmkKdiVn07og="

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

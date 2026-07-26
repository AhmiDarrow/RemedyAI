"""Embedded public key for Skills Library catalog verification.

Generated for remedy-skills Ed25519 (PyNaCl SigningKey seed).
Rotate by updating this constant and re-signing the public catalog.
"""

from __future__ import annotations

# Base64 of 32-byte Ed25519 verify key (matches REMEDY_SKILLS_SIGNING_KEY seed used in dev)
CATALOG_PUBLIC_KEY_B64 = "op2KL8LRMHmwjJfywQWgZthCcfwkEt/VmkKdiVn07og="

# Official catalog locations (GitHub)
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/AhmiDarrow/remedy-skills/main/catalog.json"
)
DEFAULT_CATALOG_SIG_URL = DEFAULT_CATALOG_URL + ".sig"
LIBRARY_REPO = "AhmiDarrow/remedy-skills"

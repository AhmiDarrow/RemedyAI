"""Remedy Vault — handle-based secret store for payment info and credentials.

Threat model (docs/LIFE_TASK_PARTNER.md; AGENTS.md north star Q3)
-----------------------------------------------------------------
Remedy stores payment information so it can purchase online for its owner.
Two attacker classes matter:

* **Human attackers** — disk theft, other local accounts, accidental backup or
  share of ``~/.remedy``. Answer: *audited* cryptography, never invented —
  libsodium (PyNaCl) ``SecretBox`` (XSalsa20-Poly1305 authenticated encryption)
  under a 32-byte master key sealed by Windows **DPAPI** (user-scoped) or an
  owner passphrase via **Argon2id** (``nacl.pwhash``); owner-only file modes
  elsewhere. New/novel ciphers are explicitly rejected: unvetted math is the
  one thing both attacker classes beat.

* **AI attackers** (prompt injection, poisoned pages, model exfiltration) —
  answer: an *architectural* property, not a cipher. Secrets are
  **non-representable in model context**: the model, the transcript, logs, and
  job files only ever see opaque handles (``{{vault:card-visa}}``). Plaintext
  exists transiently, machine-side, on the path from vault to a verified
  target field — and every use is (a) **domain-bound** (a card handle bound to
  ``amazon.com`` will not decrypt for any other site) and (b) behind the
  non-waivable owner checkpoint in :mod:`remedy.core.approvals`. A
  prompt-injected model can at most *reference* a handle; it can never read,
  print, or send the value.

Owner flows
-----------
* Secrets ENTER the vault through the owner's own UI (Settings → Vault or
  ``/vault add``), never through chat — so the value never transits model
  context even once. ``vault_add`` is therefore not registered as an agent
  tool; only listing handles is.
* Secrets LEAVE the vault only via :func:`reveal_for_use`, which enforces
  domain binding and records an audit line (handle + destination, never the
  value).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nacl import pwhash, secret
from nacl import utils as nacl_utils

from remedy.interfaces.secret_store import (
    _dpapi_available,
    _dpapi_protect,
    _dpapi_unprotect,
    _harden_path,
    auth_dir,
)

logger = logging.getLogger(__name__)

VAULT_VERSION = 1
VAULT_FILENAME = "vault.json"
VAULT_KEY_FILENAME = "vault.key"

# The only way a secret is ever referenced in model context / transcripts.
VAULT_TOKEN_RE = re.compile(r"\{\{\s*vault:([a-z0-9][a-z0-9._-]{0,63})\s*\}\}")

_KINDS = ("card", "password", "note", "address", "identity")

_lock = threading.RLock()


class VaultError(RuntimeError):
    """Base vault error (safe to surface: never contains secret material)."""


class VaultLockedError(VaultError):
    """Master key unavailable (bad passphrase / missing DPAPI)."""


class VaultDomainError(VaultError):
    """Handle is domain-bound and the destination does not match."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def vault_path(home: Path | str | None = None) -> Path:
    return auth_dir(home) / VAULT_FILENAME


def key_path(home: Path | str | None = None) -> Path:
    return auth_dir(home) / VAULT_KEY_FILENAME


# ---------------------------------------------------------------------------
# Master key (KEK) — sealed at rest, 32 bytes for SecretBox
# ---------------------------------------------------------------------------


def _new_master_key() -> bytes:
    return nacl_utils.random(secret.SecretBox.KEY_SIZE)


def _seal_master_key(
    key: bytes, *, passphrase: str | None = None
) -> dict[str, Any]:
    """Seal the 32-byte master key for disk. Never stores it raw when a
    stronger seal is available (passphrase > DPAPI > owner-only file mode)."""
    if passphrase:
        salt = nacl_utils.random(pwhash.argon2id.SALTBYTES)
        kek = pwhash.argon2id.kdf(
            secret.SecretBox.KEY_SIZE,
            passphrase.encode("utf-8"),
            salt,
            opslimit=pwhash.argon2id.OPSLIMIT_MODERATE,
            memlimit=pwhash.argon2id.MEMLIMIT_MODERATE,
        )
        sealed = secret.SecretBox(kek).encrypt(key)
        return {
            "seal": "argon2id",
            "salt": base64.b64encode(salt).decode("ascii"),
            "payload": base64.b64encode(bytes(sealed)).decode("ascii"),
        }
    if _dpapi_available():
        try:
            return {
                "seal": "dpapi",
                "payload": base64.b64encode(_dpapi_protect(key)).decode("ascii"),
            }
        except Exception as exc:  # pragma: no cover - windows only
            logger.warning("vault: DPAPI seal failed (%s); using file-mode seal", exc)
    return {
        "seal": "plain",
        "payload": base64.b64encode(key).decode("ascii"),
    }


def _unseal_master_key(
    blob: dict[str, Any], *, passphrase: str | None = None
) -> bytes:
    seal = str(blob.get("seal") or "")
    payload = base64.b64decode(str(blob.get("payload") or ""))
    if seal == "argon2id":
        if not passphrase:
            raise VaultLockedError(
                "Vault is passphrase-locked — the owner must unlock it first."
            )
        salt = base64.b64decode(str(blob.get("salt") or ""))
        kek = pwhash.argon2id.kdf(
            secret.SecretBox.KEY_SIZE,
            passphrase.encode("utf-8"),
            salt,
            opslimit=pwhash.argon2id.OPSLIMIT_MODERATE,
            memlimit=pwhash.argon2id.MEMLIMIT_MODERATE,
        )
        try:
            return secret.SecretBox(kek).decrypt(payload)
        except Exception as exc:
            raise VaultLockedError("Wrong vault passphrase.") from exc
    if seal == "dpapi":
        try:
            return _dpapi_unprotect(payload)
        except Exception as exc:  # pragma: no cover - windows only
            raise VaultLockedError(f"DPAPI unseal failed: {exc}") from exc
    if seal == "plain":
        return payload
    raise VaultLockedError(f"Unknown vault key seal: {seal!r}")


def _load_or_create_master_key(
    home: Path | str | None = None, *, passphrase: str | None = None
) -> bytes:
    kp = key_path(home)
    if kp.exists():
        blob = json.loads(kp.read_text(encoding="utf-8"))
        return _unseal_master_key(blob, passphrase=passphrase)
    key = _new_master_key()
    kp.write_text(
        json.dumps(_seal_master_key(key, passphrase=passphrase)) + "\n",
        encoding="utf-8",
    )
    _harden_path(kp, is_dir=False)
    return key


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@dataclass
class VaultItem:
    handle: str
    kind: str
    label: str
    domains: list[str] = field(default_factory=list)
    preview: str = ""  # non-secret hint the owner chose (e.g. "Visa …4242")
    ciphertext_b64: str = ""
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        """Model/UI-safe view: handle + metadata, NEVER the value."""
        return {
            "handle": self.handle,
            "token": "{{vault:" + self.handle + "}}",
            "kind": self.kind,
            "label": self.label,
            "domains": list(self.domains),
            "preview": self.preview,
            "created_at": self.created_at,
        }


def _load_items(home: Path | str | None = None) -> dict[str, VaultItem]:
    path = vault_path(home)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("vault: unreadable store; treating as empty (not overwriting)")
        return {}
    out: dict[str, VaultItem] = {}
    for entry in raw.get("items") or []:
        if not isinstance(entry, dict):
            continue
        h = str(entry.get("handle") or "")
        if not h:
            continue
        out[h] = VaultItem(
            handle=h,
            kind=str(entry.get("kind") or "note"),
            label=str(entry.get("label") or h),
            domains=[str(d) for d in (entry.get("domains") or [])],
            preview=str(entry.get("preview") or ""),
            ciphertext_b64=str(entry.get("ciphertext_b64") or ""),
            created_at=float(entry.get("created_at") or 0.0),
        )
    return out


def _save_items(items: dict[str, VaultItem], home: Path | str | None = None) -> None:
    path = vault_path(home)
    data = {
        "version": VAULT_VERSION,
        "updated_at": time.time(),
        "items": [
            {
                "handle": it.handle,
                "kind": it.kind,
                "label": it.label,
                "domains": it.domains,
                "preview": it.preview,
                "ciphertext_b64": it.ciphertext_b64,
                "created_at": it.created_at,
            }
            for it in items.values()
        ],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _harden_path(tmp, is_dir=False)
    tmp.replace(path)
    _harden_path(path, is_dir=False)


def _normalize_handle(handle: str) -> str:
    h = re.sub(r"[^a-z0-9._-]+", "-", (handle or "").strip().lower()).strip("-")
    if not h:
        raise VaultError("Vault handle is required (e.g. 'card-visa').")
    if len(h) > 64:
        h = h[:64]
    return h


def _normalize_domain(d: str) -> str:
    d = (d or "").strip().lower()
    if "://" in d:
        d = urlparse(d).hostname or d
    return d.removeprefix("www.").strip("/")


# ---------------------------------------------------------------------------
# Public API — owner-side
# ---------------------------------------------------------------------------


def vault_add(
    *,
    value: str,
    handle: str,
    kind: str = "note",
    label: str = "",
    domains: list[str] | None = None,
    preview: str = "",
    home: Path | str | None = None,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Store a secret. Owner-side only (Settings UI / CLI) — never an agent tool.

    Returns the item's public view (no secret). ``preview`` is an owner-chosen
    non-secret hint; for cards, pass e.g. ``"Visa …4242"`` — the full value is
    never derivable from the store's metadata.
    """
    if not value:
        raise VaultError("Refusing to store an empty secret.")
    k = (kind or "note").strip().lower()
    if k not in _KINDS:
        raise VaultError(f"Unknown vault kind {kind!r}; use one of {_KINDS}.")
    h = _normalize_handle(handle)
    with _lock:
        master = _load_or_create_master_key(home, passphrase=passphrase)
        box = secret.SecretBox(master)
        sealed = box.encrypt(value.encode("utf-8"))
        items = _load_items(home)
        items[h] = VaultItem(
            handle=h,
            kind=k,
            label=(label or h).strip(),
            domains=[_normalize_domain(d) for d in (domains or []) if _normalize_domain(d)],
            preview=preview.strip(),
            ciphertext_b64=base64.b64encode(bytes(sealed)).decode("ascii"),
        )
        _save_items(items, home)
        return items[h].public()


def vault_list(home: Path | str | None = None) -> list[dict[str, Any]]:
    """Public metadata for every stored secret — safe for model context."""
    with _lock:
        return [it.public() for it in _load_items(home).values()]


def vault_delete(handle: str, home: Path | str | None = None) -> bool:
    with _lock:
        items = _load_items(home)
        h = _normalize_handle(handle)
        if h not in items:
            return False
        del items[h]
        _save_items(items, home)
        return True


# ---------------------------------------------------------------------------
# Public API — machine-side use (the only plaintext exit)
# ---------------------------------------------------------------------------


def contains_vault_token(text: str) -> bool:
    return bool(VAULT_TOKEN_RE.search(text or ""))


def token_handles(text: str) -> list[str]:
    return [m.group(1) for m in VAULT_TOKEN_RE.finditer(text or "")]


def reveal_for_use(
    handle: str,
    *,
    destination_url: str = "",
    home: Path | str | None = None,
    passphrase: str | None = None,
) -> str:
    """Decrypt one secret for immediate machine-side use.

    Enforces **domain binding**: an item with ``domains`` set only decrypts
    when *destination_url*'s host matches (exact or subdomain). Every reveal is
    audited (handle + destination host, never the value). Callers must pass the
    value straight to the input path and drop it — never into results, logs,
    or model context.
    """
    h = _normalize_handle(handle)
    with _lock:
        items = _load_items(home)
        item = items.get(h)
        if item is None:
            raise VaultError(f"No vault item {h!r}. Use vault_list for handles.")
        dest = _normalize_domain(destination_url)
        if item.domains:
            allowed = any(
                dest == d or dest.endswith("." + d) for d in item.domains if d
            )
            if not allowed:
                raise VaultDomainError(
                    f"Vault item {h!r} is bound to {item.domains} and will not "
                    f"decrypt for {dest or '(unknown destination)'!r}. "
                    "If this is intentional, the owner can update the binding "
                    "in Settings → Vault."
                )
        master = _load_or_create_master_key(home, passphrase=passphrase)
        box = secret.SecretBox(master)
        try:
            value = box.decrypt(base64.b64decode(item.ciphertext_b64)).decode("utf-8")
        except Exception as exc:
            raise VaultLockedError(
                f"Vault item {h!r} failed to decrypt (key mismatch or corrupt store)."
            ) from exc
    try:
        from remedy.core.computer.audit import log_computer_action

        log_computer_action(
            action="vault_reveal",
            target=dest or "unknown",
            ok=True,
            detail={"handle": h, "kind": item.kind},
        )
    except Exception:
        pass
    return value


def expand_text(
    text: str,
    *,
    destination_url: str = "",
    home: Path | str | None = None,
    passphrase: str | None = None,
) -> tuple[str, list[str]]:
    """Replace every ``{{vault:handle}}`` token with its secret value.

    Returns ``(expanded_text, handles_used)``. Raises :class:`VaultError` /
    :class:`VaultDomainError` on unknown handles or domain mismatch — callers
    surface the message (it never contains secrets) and do NOT type anything.
    """
    handles: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        h = m.group(1)
        handles.append(h)
        return reveal_for_use(
            h, destination_url=destination_url, home=home, passphrase=passphrase
        )

    return VAULT_TOKEN_RE.sub(_sub, text or ""), handles
